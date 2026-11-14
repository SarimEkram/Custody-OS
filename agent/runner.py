"""Runs one transfer: build the run context, drive the agent loop, and on a halt hand over
to the recovery planner. Intake calls start() once a validated request has a transfer.

A crash inside a run is kept in `errors` (not swallowed). The transfer's state only ever moved
on scan-verified steps, so restarting from `state` is safe (see "Crash/restart" in
ARCHITECTURE.md; resuming is a stretch item).
"""

import asyncio
import logging
from dataclasses import dataclass

from agent.executor import RunContext
from agent.loop import AgentLoop, RunResult
from agent.recovery import RecoveryPlanner, RecoveryResult
from agent.transfers import MemoryCustodyStore

log = logging.getLogger("custody.runner")


@dataclass
class Outcome:
    run: RunResult
    recovery: RecoveryResult | None = None


# runs that ended abnormally (budget, model outage, crash): they ask for recovery like a halt does
ABORTED = ("max_steps", "budget", "llm_error", "error")


class Runner:
    def __init__(
        self,
        loop: AgentLoop,
        planner: RecoveryPlanner,
        store: MemoryCustodyStore,
        requests,
        agent_id: str = "agent:custody-01",
    ) -> None:
        self.loop, self.planner, self.store = loop, planner, store
        self.requests, self.agent_id = requests, agent_id
        self.errors: dict[str, BaseException] = {}
        self.loop_tasks: dict[str, asyncio.Task] = {}
        self.halts: dict[str, str] = {}  # transfer -> who halted it
        self.tasks: set[asyncio.Task] = set()
        self.running: set[str] = set()  # transfers whose run() has not returned yet

    def _ctx(self, t: dict) -> RunContext:
        refs = {k: t[k] for k in ("sample_id", "from", "to", "window") if t.get(k)}
        return RunContext(self.agent_id, t["requested_by"], t["grant_id"], t["_id"],
                          t["request_id"], refs=refs)  # fmt: skip

    async def run(self, transfer_id: str) -> Outcome:
        self.running.add(transfer_id)
        try:
            return await self._run(transfer_id)
        finally:
            self.running.discard(transfer_id)

    async def _run(self, transfer_id: str) -> Outcome:
        t = await self.store.get_transfer(transfer_id)
        ctx = self._ctx(t)
        said = (await self.requests.get(t["request_id"]))["transcript"]
        said = said.replace("<untrusted>", "").replace("</untrusted>", "")  # cannot close its fence
        text = (
            f'The tech said: <untrusted>"{said}"</untrusted>\n'
            f"Validated: move {t['sample_id']} from {t['from']} to {t['to']}"
            # No time said means "now": without this the model stops to ask for a window, and a run
            # cannot ask the tech anything, so the transfer was cancelled before it started.
            + (f", window {t['window']}" if t.get("window") else ", window ASAP (none requested)")
            + "."
        )
        loop_task = asyncio.ensure_future(self.loop.run(text, ctx))
        self.loop_tasks[transfer_id] = loop_task
        try:
            result = await loop_task
        except asyncio.CancelledError:
            if transfer_id not in self.halts:  # a real shutdown, not a halt
                raise
            result = RunResult("halted")
        finally:
            self.loop_tasks.pop(transfer_id, None)
        recover = result.status == "halted" or transfer_id in self.halts or (
            result.status in ("denied", "timeout", *ABORTED) and result.needs_recovery
        )
        if not recover:
            # The run ended before anything left storage (e.g. the model gave up after a denial):
            # roll back what it booked and close the transfer, or it stays "requested" (looks busy).
            state = (await self.store.get_transfer(transfer_id))["state"]
            recover = state in ("requested", "reserved") or transfer_id in self.halts
        if not recover:
            return Outcome(result)
        stored = (await self.store.get_transfer(transfer_id)).get("halted_by")
        revoker = self.halts.get(transfer_id) or stored or t["requested_by"]
        return Outcome(result, await self.planner.run(ctx, revoker))

    async def halt(self, transfer_id: str, by: str) -> None:
        """A revoke/Stop arrived: stop the run wherever it is (even mid-tool) for recovery."""
        if transfer_id in self.halts:
            return
        self.halts[transfer_id] = by
        task = self.loop_tasks.get(transfer_id)
        if task and not task.done():
            task.cancel()
        elif transfer_id not in self.running:
            # No run to hand over (it already ended, or the agent restarted): recover here, or the
            # transfer stays "revoked" for good.
            self._track(transfer_id, self.recover(transfer_id, by))

    async def recover(self, transfer_id: str, by: str) -> Outcome:
        ctx = self._ctx(await self.store.get_transfer(transfer_id))
        return Outcome(RunResult("halted"), await self.planner.run(ctx, by))

    def start(self, transfer_id: str) -> asyncio.Task:
        return self._track(transfer_id, self.run(transfer_id))

    def _track(self, transfer_id: str, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self.tasks.discard(t)
            if not t.cancelled() and t.exception():
                self.errors[transfer_id] = t.exception()
                log.error("run for %s failed", transfer_id, exc_info=t.exception())

        task.add_done_callback(done)
        return task

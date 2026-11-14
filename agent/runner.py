"""Runs one transfer: build the run context, drive the agent loop, and on a halt hand over
to the recovery planner. Intake calls start() once a validated request has a transfer.

A crash inside a run is kept in `errors` (not swallowed). The transfer's state only ever moved
on scan-verified steps, so restarting from `state` is safe (see "Crash/restart" in
ARCHITECTURE.md; resuming is a stretch item).
"""

import asyncio
from dataclasses import dataclass

from agent.executor import RunContext
from agent.loop import AgentLoop, RunResult
from agent.recovery import RecoveryPlanner, RecoveryResult
from agent.transfers import MemoryCustodyStore


@dataclass
class Outcome:
    run: RunResult
    recovery: RecoveryResult | None = None


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

    async def run(self, transfer_id: str) -> Outcome:
        t = await self.store.get_transfer(transfer_id)
        refs = {k: t[k] for k in ("sample_id", "from", "to", "window") if t.get(k)}
        ctx = RunContext(self.agent_id, t["requested_by"], t["grant_id"], transfer_id,
                         t["request_id"], refs=refs)  # fmt: skip
        said = (await self.requests.get(t["request_id"]))["transcript"]
        text = (
            f'The tech said: "{said}"\n'
            f"Validated: move {t['sample_id']} from {t['from']} to {t['to']}"
            + (f", window {t['window']}" if t.get("window") else "")
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
        recover = result.status == "halted" or (
            result.status in ("denied", "timeout") and result.needs_recovery
        )
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

    def start(self, transfer_id: str) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(self.run(transfer_id))
        self.tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self.tasks.discard(t)
            if t.exception():
                self.errors[transfer_id] = t.exception()

        task.add_done_callback(done)
        return task

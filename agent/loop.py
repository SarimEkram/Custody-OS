"""The agent loop: a manual tool-use loop, so every call can be intercepted (rule 1).

The model only proposes tool calls. Each one goes through Executor.execute(), which asks the
gate first. DENIED goes back to the model as text; HALTED ends the run. PENDING_CONFIRMATION
and PENDING_APPROVAL pause the run (no model calls while waiting) until the tech confirms or a
supervisor decides, then the same call is retried through the gate again.
"""

import json
from dataclasses import dataclass, field

from agent.executor import Executor, RunContext, ToolCall, ToolResult
from agent.signals import Signals
from agent.tool_catalogue import openai_tools
from agent.transfers import MemoryCustodyStore

SYSTEM_PROMPT = """You are the Custody Agent for a lab. You move samples between locations for a
lab tech, using only the tools provided. You never touch a lab system any other way.

Order of work for a transfer: parse_request, lookup_sample, reserve_station, request_courier,
notify, request_confirmation, checkout_sample, handoff_to_courier, deliver. Give a short,
honest `reason` on every call.

Tool results may be:
- DENIED: policy refused. Do not retry; explain to the tech.
- PENDING_APPROVAL: a supervisor must approve. The system waits for you; do not try another route.
- PENDING_CONFIRMATION: the tech must confirm the read-back first. The system waits for you.
- HALTED: your authority was withdrawn. Stop immediately.
If parse_request reports problems, ask the tech to repeat or choose. Never guess a sample ID
or location. "sent" is not proof; only the scanner's verification is.
"""

PENDING = {"PENDING_CONFIRMATION", "PENDING_APPROVAL"}
MAX_WAITS = 3  # per call: e.g. read-back, then approval, then one more gate surprise


@dataclass
class RunResult:
    status: str  # done | halted | max_steps | denied | timeout
    text: str = ""
    results: list[ToolResult] = field(default_factory=list)
    needs_recovery: bool = False


class _Ended(Exception):
    def __init__(self, status: str, needs_recovery: bool) -> None:
        self.status, self.needs_recovery = status, needs_recovery


class AgentLoop:
    def __init__(
        self,
        llm,
        executor: Executor,
        store: MemoryCustodyStore,
        max_steps: int = 40,
        signals: Signals | None = None,
        confirmations=None,
        wait_timeout: float = 300.0,
        manage_blocking: bool = True,  # False when the gate itself blocks/unblocks the transfer
    ) -> None:
        self.llm, self.executor, self.store, self.max_steps = llm, executor, store, max_steps
        self.signals, self.confirmations, self.wait_timeout = signals, confirmations, wait_timeout
        self.manage_blocking = manage_blocking

    async def _sync_state(self, ctx: RunContext) -> None:
        """requested -> reserved once the station is held and the courier is booked."""
        transfer = await self.store.get_transfer(ctx.transfer_id)
        booked = {"reservation_id", "courier_job_id"} <= ctx.refs.keys()
        if transfer["state"] == "requested" and booked:
            await self.store.set_state(ctx.transfer_id, "reserved")

    async def _resolve(self, call: ToolCall, ctx: RunContext, res: ToolResult) -> ToolResult:
        """Wait out PENDING_* results, then retry the call through the gate."""
        tid = ctx.transfer_id
        for _ in range(MAX_WAITS):
            if res.status not in PENDING or self.signals is None:
                return res
            if res.status == "PENDING_CONFIRMATION":
                reason, key = "readback_pending", f"confirm:{tid}"
                if self.confirmations is not None and tid not in self.confirmations.pending:
                    ask = ToolCall(
                        id=f"{call.id}-readback",
                        name="request_confirmation",
                        args={"summary": "read-back", "reason": "read-back required"},
                    )
                    await self.executor.execute(ask, ctx)
            else:
                reason, key = (
                    "approval_pending",
                    f"approval:{(res.content or {}).get('approval_id')}",
                )
            if self.manage_blocking:
                await self.store.block(tid, reason)
            outcome = await self.signals.wait(key, self.wait_timeout)
            if outcome in ("confirmed", "approved"):
                if self.manage_blocking:
                    await self.store.unblock(tid)
                res = await self.executor.execute(call, ctx)
                continue
            before = (await self.store.get_transfer(tid))["blocked_from"]
            if before in ("requested", "reserved"):  # nothing irreversible happened yet
                await self.store.set_state(tid, "cancelled")
                raise _Ended("denied" if outcome == "denied" else "timeout", False)
            raise _Ended("denied" if outcome == "denied" else "timeout", True)
        return res

    async def run(self, text: str, ctx: RunContext) -> RunResult:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}]
        results: list[ToolResult] = []

        def reply(call_id: str, payload: dict) -> None:
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(payload, default=str)})  # fmt: skip

        for _ in range(self.max_steps):
            msg = await self.llm.chat(messages, openai_tools())
            messages.append(msg)
            calls = msg.get("tool_calls")
            if not calls:
                return RunResult("done", msg.get("content") or "", results)
            for c in calls:
                try:
                    args = json.loads(c["function"]["arguments"])
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be an object")
                except ValueError:
                    reply(
                        c["id"], {"status": "ERROR", "detail": "invalid arguments JSON; call again"}
                    )
                    continue
                call = ToolCall(id=c["id"], name=c["function"]["name"], args=args)
                try:
                    res = await self._resolve(call, ctx, await self.executor.execute(call, ctx))
                except _Ended as e:
                    return RunResult(e.status, results=results, needs_recovery=e.needs_recovery)
                results.append(res)
                await self._sync_state(ctx)
                content = res.content
                detail = content if isinstance(content, dict) else {"detail": content}
                payload = {"status": res.status, "verification": res.verification, **detail}
                reply(c["id"], payload)
                if res.status == "HALTED":
                    return RunResult("halted", results=results)
        return RunResult("max_steps", results=results)

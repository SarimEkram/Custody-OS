"""The agent loop: a manual tool-use loop, so every call can be intercepted (rule 1).

The model only proposes tool calls. Each one goes through Executor.execute(), which asks the
gate first. DENIED / PENDING_* results go back to the model as text; HALTED ends the run.
"""

import json
from dataclasses import dataclass, field

from agent.executor import Executor, RunContext, ToolCall, ToolResult
from agent.tools import openai_tools
from agent.transfers import MemoryCustodyStore

SYSTEM_PROMPT = """You are the Custody Agent for a lab. You move samples between locations for a
lab tech, using only the tools provided. You never touch a lab system any other way.

Order of work for a transfer: parse_request, lookup_sample, reserve_station, request_courier,
notify, checkout_sample, handoff_to_courier, deliver. Give a short, honest `reason` on every call.

Tool results may be:
- DENIED: policy refused. Do not retry; explain to the tech.
- PENDING_APPROVAL: a supervisor must approve. Stop and wait; do not try another route.
- PENDING_CONFIRMATION: the tech must confirm the read-back first.
- HALTED: your authority was withdrawn. Stop immediately.
If parse_request reports problems, ask the tech to repeat or choose. Never guess a sample ID
or location. "sent" is not proof; only the scanner's verification is.
"""


@dataclass
class RunResult:
    status: str  # done | halted | max_steps
    text: str = ""
    results: list[ToolResult] = field(default_factory=list)


class AgentLoop:
    def __init__(self, llm, executor: Executor, store: MemoryCustodyStore, max_steps: int = 40):
        self.llm, self.executor, self.store, self.max_steps = llm, executor, store, max_steps

    def _sync_state(self, ctx: RunContext) -> None:
        """requested -> reserved once the station is held and the courier is booked."""
        transfer = self.store.transfers[ctx.transfer_id]
        if (
            transfer["state"] == "requested"
            and {"reservation_id", "courier_job_id"} <= ctx.refs.keys()
        ):
            self.store.set_state(ctx.transfer_id, "reserved")

    async def run(self, text: str, ctx: RunContext) -> RunResult:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}]
        results: list[ToolResult] = []
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
                    payload = {"status": "ERROR", "detail": "invalid arguments JSON; call again"}
                else:
                    res = await self.executor.execute(
                        ToolCall(id=c["id"], name=c["function"]["name"], args=args), ctx
                    )
                    results.append(res)
                    self._sync_state(ctx)
                    detail = (
                        res.content if isinstance(res.content, dict) else {"detail": res.content}
                    )
                    payload = {"status": res.status, "verification": res.verification, **detail}
                    if res.status == "HALTED":
                        messages.append({"role": "tool", "tool_call_id": c["id"],
                                         "content": json.dumps(payload, default=str)})  # fmt: skip
                        return RunResult("halted", results=results)
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": json.dumps(payload, default=str)})  # fmt: skip
        return RunResult("max_steps", results=results)

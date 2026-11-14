"""Tool executor: every tool call goes through here (rules 1 and 2).

check() -> if not allowed, stop (the lab is never called) -> call the lab -> write the
outcome entry -> for physical tools, ask the verifier for a scan-based confirmation.
The agent's own word is never the proof: `status: "sent"` only means the lab accepted it.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

from agent.lab import LabClient, LabError
from agent.tools import TOOLS

STATUS_FOR_DECISION = {
    "deny": "DENIED",
    "pending_approval": "PENDING_APPROVAL",
    "pending_confirmation": "PENDING_CONFIRMATION",
    "halt": "HALTED",
}


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict


class ToolResult(BaseModel):
    tool_call_id: str
    status: Literal["OK", "DENIED", "PENDING_APPROVAL", "PENDING_CONFIRMATION", "HALTED", "ERROR"]
    correlation_id: str | None = None
    content: Any = None
    verification: str | None = None


@dataclass
class RunContext:
    actor: str
    on_behalf_of: str
    grant_id: str
    transfer_id: str
    request_id: str | None = None
    refs: dict = field(default_factory=dict)  # working memory: sample_id, from, to, lab ids


Internal = Callable[[dict, RunContext], Awaitable[dict]]


def _new_cid() -> str:
    return f"c-{uuid.uuid4().hex[:8]}"


class Executor:
    def __init__(
        self,
        gate,
        lab: LabClient,
        append: Callable[[dict], Awaitable[Any]],
        verifier=None,
        internal: dict[str, Internal] | None = None,
        new_cid: Callable[[], str] = _new_cid,
    ) -> None:
        self.gate, self.lab, self.append = gate, lab, append
        self.verifier, self.internal, self.new_cid = verifier, internal or {}, new_cid

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        tool = TOOLS.get(call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=call.id, status="ERROR", content=f"unknown tool {call.name}"
            )
        cid = self.new_cid()
        reason = call.args.get("reason", "")
        decision = await self.gate.check(
            actor=ctx.actor, on_behalf_of=ctx.on_behalf_of, grant_id=ctx.grant_id,
            transfer_id=ctx.transfer_id, tool=call.name, args=call.args, reason=reason,
            correlation_id=cid,
        )  # fmt: skip
        if decision.decision != "allow":
            return ToolResult(
                tool_call_id=call.id,
                status=STATUS_FOR_DECISION[decision.decision],
                correlation_id=cid,
                content={"rule_hits": decision.rule_hits, "approval_id": decision.approval_id},
            )

        response, error = None, None
        try:
            if tool.system == "internal":
                if call.name not in self.internal:
                    raise LabError(501, f"no handler for internal tool {call.name}")
                response = await self.internal[call.name](call.args, ctx)
            else:
                response = await self.lab.call(call.name, call.args, cid, ctx.refs)
        except LabError as e:
            error = str(e)

        outcome_id = await self.append({
            "timestamp": datetime.now(UTC), "entry_type": "outcome", "actor": ctx.actor,
            "on_behalf_of": ctx.on_behalf_of, "grant_id": ctx.grant_id,
            "policy_id": decision.policy_id, "policy_version": decision.policy_version,
            "decision": "allow", "transfer_id": ctx.transfer_id,
            "sample_id": ctx.refs.get("sample_id"),
            "request_id": ctx.request_id, "correlation_id": cid, "tool": call.name,
            "args": call.args, "reversible": tool.reversible, "reason": reason,
            "request": call.args, "response": {"error": error} if error else response,
            "status": "sent" if tool.expected_scan and not error else None,
            "refs": [decision.ledger_id],
            "summary": f"{ctx.actor} called {call.name} for {ctx.on_behalf_of}: {reason}",
        })  # fmt: skip
        if error:
            return ToolResult(
                tool_call_id=call.id, status="ERROR", correlation_id=cid, content=error
            )

        verification = None
        if tool.expected_scan and self.verifier:
            verification = await self.verifier.confirm(
                correlation_id=cid, tool=call.name, ctx=ctx, outcome_id=outcome_id
            )
        return ToolResult(tool_call_id=call.id, status="OK", correlation_id=cid, content=response,
                          verification=verification)  # fmt: skip

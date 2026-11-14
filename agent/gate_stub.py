"""STUB: stands in for A's policy gate (agent/policy_gate.py) until it exists.

It allows everything. It still writes a decision entry first, so the executor's ordering
(decision before action, rule 2) is exercised for real. Delete this file when A delivers
`check()`; GateDecision here mirrors the contract in docs/ARCHITECTURE.md.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel


class GateDecision(BaseModel):
    decision: Literal["allow", "deny", "pending_approval", "pending_confirmation", "halt"]
    policy_id: str
    policy_version: int
    rule_hits: list[str] = []
    risk_flags: list[str] = []
    approval_id: str | None = None
    ledger_id: Any


class StubGate:
    def __init__(self, append: Callable[[dict], Awaitable[Any]]) -> None:
        self.append = append

    async def check(
        self, *, actor, on_behalf_of, grant_id, transfer_id, tool, args, reason, correlation_id
    ) -> GateDecision:
        ledger_id = await self.append({
            "timestamp": datetime.now(UTC), "entry_type": "decision", "actor": actor,
            "on_behalf_of": on_behalf_of, "grant_id": grant_id, "policy_id": "STUB",
            "policy_version": 1, "decision": "allow", "transfer_id": transfer_id,
            "correlation_id": correlation_id, "tool": tool, "args": args, "reason": reason,
        })  # fmt: skip
        return GateDecision(decision="allow", policy_id="STUB", policy_version=1,
                            ledger_id=ledger_id)  # fmt: skip

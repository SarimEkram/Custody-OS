"""Tool catalogue (shared contract between the gate and B's tool executor).

Every tool the agent can call is listed here with its reversibility, compensation/recovery,
expected scan and which grant/policy rules apply. When you add a tool, add it here first
(CLAUDE.md: gate rules + reversible tag + compensation + expected scan).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Reversibility = Literal["read_only", "reversible", "irreversible", "recovery"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    system: Literal["internal", "freezer", "scheduler", "courier", "station"]
    reversibility: Reversibility
    compensation: str | None = None  # tool that undoes it (reversible) or recovery step (irreversible)
    expected_scan: str | None = None  # custody_events.type the verifier waits for
    needs_scope: bool = True  # must appear in grant.scope.actions
    moves_toward_destination: bool = False  # destination certification applies

    @property
    def reversible(self) -> bool | None:
        """Value stored in ledger.reversible (None for read-only)."""
        return {"read_only": None, "reversible": True, "irreversible": False, "recovery": True}[self.reversibility]

    @property
    def irreversible(self) -> bool:
        return self.reversibility == "irreversible"


TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("parse_request", "internal", "read_only", needs_scope=False),
    ToolSpec("request_confirmation", "internal", "read_only", needs_scope=False),
    ToolSpec("lookup_sample", "freezer", "read_only"),
    ToolSpec("reserve_station", "scheduler", "reversible", compensation="cancel_reservation",
             moves_toward_destination=True),
    ToolSpec("request_courier", "courier", "reversible", compensation="cancel_courier_job",
             moves_toward_destination=True),
    ToolSpec("notify", "internal", "reversible", compensation="notify"),  # retraction notice
    ToolSpec("checkout_sample", "freezer", "irreversible", compensation="return_to_storage",
             expected_scan="checkout", moves_toward_destination=True),
    ToolSpec("handoff_to_courier", "courier", "irreversible", compensation="return_to_storage",
             expected_scan="handoff", moves_toward_destination=True),
    ToolSpec("deliver", "station", "irreversible", compensation="return_to_storage",
             expected_scan="intake", moves_toward_destination=True),
    # recovery-only tools: allowed under the recovery policy even without a valid grant
    ToolSpec("cancel_reservation", "scheduler", "recovery", needs_scope=False),
    ToolSpec("cancel_courier_job", "courier", "recovery", needs_scope=False),
    ToolSpec("return_to_storage", "courier", "recovery", expected_scan="checkin", needs_scope=False),
]}


def spec(tool: str) -> ToolSpec:
    try:
        return TOOLS[tool]
    except KeyError:
        raise ValueError(f"unknown tool {tool!r}; add it to agent/tools.py first") from None

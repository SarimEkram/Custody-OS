"""Guardrails on the model-to-gate boundary (GUARDRAILS_SPEC.md).

Pure functions only: no database, no network. The gate (`policy_gate`) calls `validate_call` and
`bound_mismatches`; the agent loop reads `limits()` for its run budgets. Nothing here can allow a
call: it can only refuse one, and every refusal is written to the ledger by the gate.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from agent.tool_catalogue import TOOLS as CATALOGUE
from agent.tools import TOOLS as GATE_TOOLS

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
ID_FIELDS = {"sample_id", "station_id", "from", "to", "freezer_id", "transfer_id",
             "reservation_id", "job_id", "user_id"}  # fmt: skip
OPTIONAL_FIELDS = {"window"}  # the requested time may be left out
# argument -> the transfer field it must agree with (the plan the tech confirmed)
BOUND_TO_TRANSFER = {"sample_id": "sample_id", "from": "from", "to": "to",
                     "station_id": "to", "transfer_id": "_id"}  # fmt: skip
UNBOUND_TOOLS = {"parse_request"}  # records the model's reading of the request; the transfer rules


@dataclass(frozen=True)
class Refusal:
    rule: str  # unknown_tool | invalid_arguments
    detail: str


@dataclass(frozen=True)
class Limits:
    max_text_len: int = 500
    max_reason_len: int = 500
    max_calls_per_tool: int = 3  # model-issued calls to one tool in one run
    max_repeat_refused: int = 3  # the same refused call, repeated


def limits(env: Mapping[str, str] | None = None) -> Limits:
    env = os.environ if env is None else env
    d = Limits()

    def num(name: str, default: int) -> int:
        try:
            return max(1, int(env[name]))
        except (KeyError, ValueError):
            return default

    return Limits(
        num("GUARDRAIL_MAX_TEXT_LEN", d.max_text_len),
        num("GUARDRAIL_MAX_REASON_LEN", d.max_reason_len),
        num("GUARDRAIL_MAX_CALLS_PER_TOOL", d.max_calls_per_tool),
        num("GUARDRAIL_MAX_REPEAT_REFUSED", d.max_repeat_refused),
    )


def validate_call(tool: str, args: dict, reason: str = "") -> Refusal | None:
    """None if the call is well formed; otherwise why it is not (names the offending fields)."""
    if tool not in CATALOGUE or tool not in GATE_TOOLS:
        return Refusal("unknown_tool", f"{tool!r} is not a tool; use only the tools provided")
    lim = limits()
    params = set(CATALOGUE[tool].params)
    problems: list[str] = []
    problems += [f"{k}: unknown argument" for k in sorted(set(args) - params - {"reason"})]
    problems += [f"{k}: required" for k in sorted(params - OPTIONAL_FIELDS - set(args))]
    for k in sorted(params & set(args)):
        v = args[k]
        if not isinstance(v, str):
            problems.append(f"{k}: must be a string")
        elif k in ID_FIELDS and not ID_RE.match(v):
            problems.append(f"{k}: must be an id such as TS-1042, not free text")
        elif k not in ID_FIELDS and len(v) > lim.max_text_len:
            problems.append(f"{k}: longer than {lim.max_text_len} characters")
    if not isinstance(reason, str) or len(reason) > lim.max_reason_len:
        problems.append(f"reason: must be text of at most {lim.max_reason_len} characters")
    if problems:
        return Refusal("invalid_arguments", "; ".join(problems))
    return None


def bound_mismatches(tool: str, args: dict, transfer: dict | None) -> list[str]:
    """Arguments that contradict the transfer the tech confirmed. Omitting one is fine: the
    system fills it from the transfer. Naming a different sample, route or transfer is not."""
    if not transfer or tool in UNBOUND_TOOLS:
        return []
    return sorted(
        f"{arg} {args[arg]!r} is not {transfer[field]!r}"
        for arg, field in BOUND_TO_TRANSFER.items()
        if arg in args and transfer.get(field) is not None and args[arg] != transfer[field]
    )


def abort_entry(ctx, status: str, detail: str) -> dict:
    """The ledger row for a run that ended abnormally (budget, model outage, crash), so an auditor
    can tell "the agent broke or ran out of budget" from "the gate said no"."""
    import uuid
    from datetime import UTC, datetime  # local: keeps the module's top-level imports pure

    return {
        "timestamp": datetime.now(UTC),
        "entry_type": "violation",
        "actor": "system:guardrail",
        "on_behalf_of": ctx.on_behalf_of,
        "grant_id": ctx.grant_id,
        "policy_id": "guardrails",
        "policy_version": 1,
        "decision": "halt",
        "transfer_id": ctx.transfer_id,
        "sample_id": ctx.refs.get("sample_id"),
        "request_id": ctx.request_id,
        "correlation_id": f"c-{uuid.uuid4().hex[:8]}",
        "rule_hits": [status],
        "reason": detail[:500],
        "summary": f"The run for {ctx.transfer_id} ended early ({status}): {detail[:200]}",
    }

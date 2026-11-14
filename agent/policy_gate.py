"""The policy gate. Every agent tool call goes through `execute()` (CLAUDE.md rules 1 and 2).

    execute() -> check() -> [ledger: decision entry] -> (allow only) executor() -> [ledger: outcome entry]

`evaluate()` is the pure rule engine (unit-tested in tests/test_gate_rules.py). `check()` loads state
from MongoDB, runs evaluate(), writes the decision BEFORE anything happens, and opens an approval /
blocks the transfer when needed. A denied call never reaches a lab service.

Check order (first failure wins), from docs/ARCHITECTURE.md:
  1  halt flag                        -> halt
  1b read-back (voice, irreversible)  -> pending_confirmation
  2  grant valid                      -> deny
  3  scope                            -> deny
  4  restriction / clearance          -> pending_approval (or deny if the supervisor denied)
  5  destination certification        -> deny
  6  irreversible preconditions       -> deny
  7  risk (similar incidents)         -> pending_approval
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from bson import ObjectId
from pydantic import BaseModel, ConfigDict

from agent import guardrails, ledger
from agent.config import AGENT_ID
from agent.db import agent_db
from agent.embeddings import vector_stage
from agent.models import RESTRICTION_RANK, Decision, EntryType, TransferState, utcnow
from agent.tools import TOOLS, ToolSpec, spec

Executor = Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]
"""B's tool executor: (tool, args, correlation_id) -> lab service response. Must send X-Correlation-Id."""

APPROVAL_TTL = timedelta(minutes=30)
RISK_ENABLED = False  # stretch item 1; flip on once the demo path is solid


# ------------------------------------------------------------------ contracts
class GateDecision(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    decision: Decision
    policy_id: str
    policy_version: int
    rule_hits: list[str] = []
    risk_flags: list[str] = []
    approval_id: str | None = None
    ledger_id: ObjectId
    correlation_id: str
    reason: str = ""  # why, in words (shown to the model so it can correct itself)


class ToolResult(BaseModel):
    """What the agent loop feeds back to the LLM as the tool result."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    status: Literal["OK", "DENIED", "PENDING_APPROVAL", "PENDING_CONFIRMATION", "HALTED", "ERROR"]
    tool: str
    correlation_id: str
    message: str
    response: dict[str, Any] | None = None
    rule_hits: list[str] = []
    approval_id: str | None = None
    decision_id: ObjectId | None = None
    outcome_id: ObjectId | None = None
    expected_scan: str | None = None  # verifier should wait for this custody_events.type


@dataclass
class RunContext:
    transfer_id: str
    on_behalf_of: str  # user:jlee (or the revoker, in recovery mode)
    grant_id: str | None
    request_id: str | None = None
    actor: str = AGENT_ID
    mode: Literal["normal", "recovery"] = "normal"


# ------------------------------------------------------------------ pure rule engine
@dataclass
class GateState:
    """Everything evaluate() needs, loaded by check(). Plain dicts straight from MongoDB."""
    now: datetime
    actor: str
    on_behalf_of: str
    transfer: dict | None
    grant: dict | None
    sample: dict | None
    locations: dict[str, dict]
    policy: dict
    requester: dict | None = None  # identities doc for on_behalf_of
    has_confirmation: bool = False
    approval_status: Literal["approved", "denied", "pending"] | None = None
    risk_flags: list[str] = field(default_factory=list)
    mode: Literal["normal", "recovery"] = "normal"
    recovery_policy: dict | None = None
    prior_outcomes: list[dict] = field(default_factory=list)  # this transfer's earlier outcome rows


@dataclass
class Evaluation:
    decision: Decision
    rule_hits: list[str]
    reason: str
    needs_approval_request: bool = False


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def travel_minutes(locations: dict[str, dict], a: str, b: str) -> int | None:
    la, lb = locations.get(a, {}), locations.get(b, {})
    return la.get("travel_minutes", {}).get(b, lb.get("travel_minutes", {}).get(a))


def _arg_locations(args: dict) -> set[str]:
    return {v for k in ("station_id", "from", "to", "freezer_id", "location_id") if isinstance(v := args.get(k), str)}


def evaluate(tool: str, args: dict, s: GateState) -> Evaluation:
    t: ToolSpec = spec(tool)
    rules = s.policy.get("rules", {})
    tr = s.transfer or {}
    sample = s.sample or {}

    # Recovery mode: only the recovery policy's allowlist, no grant needed (docs: Stop and recovery)
    if s.mode == "recovery":
        allowed = (s.recovery_policy or {}).get("rules", {}).get("allowed_tools", [])
        if tool in allowed:
            return Evaluation(Decision.allow, ["recovery_allowlist"], f"{tool} allowed by recovery policy")
        return Evaluation(Decision.deny, ["not_in_recovery_allowlist"], f"{tool} is not a recovery action")
    if t.reversibility == "recovery":
        return Evaluation(Decision.deny, ["recovery_tool_outside_recovery"], f"{tool} only runs during recovery")

    # 1. halt flag
    if tr.get("halt"):
        return Evaluation(Decision.halt, ["halted"], tr.get("halt_reason") or "transfer halted")
    if tr.get("state") in (TransferState.revoked, TransferState.recovering, TransferState.cancelled):
        return Evaluation(Decision.halt, ["transfer_not_active"], f"transfer is {tr.get('state')}")

    # 1a. guardrails: the call must match the transfer the tech confirmed, and an irreversible
    # step that already went out must not go out twice
    mismatched = guardrails.bound_mismatches(tool, args, s.transfer)
    if mismatched:
        return Evaluation(Decision.deny, ["args_not_bound_to_transfer"],
                          "call contradicts the confirmed transfer: " + "; ".join(mismatched))
    if t.irreversible and any(o.get("tool") == tool and o.get("status") == "sent"
                              for o in s.prior_outcomes):
        return Evaluation(Decision.deny, ["duplicate_irreversible_step"],
                          f"{tool} already went out for this transfer; it cannot be repeated")

    # 1b. read-back before the first irreversible step of a voice request
    if t.irreversible and tr.get("source") == "voice" and not s.has_confirmation:
        return Evaluation(Decision.pending_confirmation, ["readback_required"],
                          "voice request: tech must confirm the read-back before anything irreversible")

    # 2. grant valid
    g = s.grant
    if g is None:
        return Evaluation(Decision.deny, ["grant_missing"], "grant not found (revoked and expired, or never issued)")
    if g.get("status") != "active":
        return Evaluation(Decision.deny, ["grant_revoked"], f"grant {g['_id']} is {g.get('status')}")
    if _as_utc(g["expires_at"]) <= s.now:
        return Evaluation(Decision.deny, ["grant_expired"], f"grant {g['_id']} expired at {g['expires_at']}")
    if g.get("grantor") != s.on_behalf_of:
        return Evaluation(Decision.deny, ["grant_wrong_grantor"], f"grant belongs to {g.get('grantor')}")
    if g.get("grantee") != s.actor:
        return Evaluation(Decision.deny, ["grant_wrong_grantee"], f"grant is delegated to {g.get('grantee')}")

    # 3. scope
    scope = g.get("scope", {})
    if t.needs_scope and tool not in scope.get("actions", []):
        return Evaluation(Decision.deny, ["action_out_of_scope"], f"{tool} is not in the grant's actions")
    if sample and scope.get("sample_types") and sample.get("type") not in scope["sample_types"]:
        return Evaluation(Decision.deny, ["sample_type_out_of_scope"], f"sample type {sample.get('type')} not in scope")
    locs = {tr.get("from"), tr.get("to")} | _arg_locations(args)
    outside = sorted(x for x in locs if x and scope.get("locations") and x not in scope["locations"])
    if outside:
        return Evaluation(Decision.deny, ["location_out_of_scope"], f"locations {outside} not in the grant's scope")
    restriction = sample.get("restriction", "standard")
    if RESTRICTION_RANK[restriction] > RESTRICTION_RANK[scope.get("max_restriction", "standard")]:
        return Evaluation(Decision.deny, ["restriction_above_grant"], f"{restriction} sample exceeds grant's max")
    if tr.get("window_start") and _as_utc(tr["window_start"]) > _as_utc(g["expires_at"]):
        return Evaluation(Decision.deny, ["window_after_grant_expiry"], "requested time is after the grant expires")

    # 4. restriction level / clearance -> supervisor approval for irreversible steps
    hits: list[str] = []
    fr = rules.get("forensic_requires_supervisor", {})
    if restriction == fr.get("restriction", "forensic") and tool in fr.get("tools", []):
        hits.append("forensic_requires_supervisor")
    if t.irreversible and s.requester is not None and restriction not in s.requester.get("clearances", []):
        hits.append("requester_lacks_clearance")
    if hits:
        if s.approval_status == "denied":
            return Evaluation(Decision.deny, hits + ["supervisor_denied"], "supervisor denied this transfer")
        if s.approval_status != "approved":
            return Evaluation(Decision.pending_approval, hits, "needs supervisor approval",
                              needs_approval_request=s.approval_status is None)

    # 5. destination certification
    if t.moves_toward_destination and rules.get("certification_required", True):
        need = set(sample.get("required_certifications", []))
        have = set(s.locations.get(tr.get("to"), {}).get("certifications", []))
        if need - have:
            return Evaluation(Decision.deny, ["destination_not_certified"],
                              f"{tr.get('to')} lacks {sorted(need - have)}")

    # 6. preconditions for irreversible tools
    if tool == "checkout_sample":
        missing = [n for n, ok in (("station_reserved", tr.get("reservation_id")),
                                   ("courier_assigned", tr.get("courier_job_id"))) if not ok]
        eta = travel_minutes(s.locations, tr.get("from"), tr.get("to"))
        limit = sample.get("max_minutes_out", 0)
        if eta is None or eta >= limit:
            missing.append("eta_within_limit")
        if sample.get("status") != "stored":
            missing.append("sample_in_storage")
        if missing:
            return Evaluation(Decision.deny, [f"precondition:{m}" for m in missing],
                              f"checkout preconditions not met: {missing} (eta {eta} min, limit {limit} min)")
    if tool == "handoff_to_courier" and tr.get("state") != TransferState.checked_out:
        return Evaluation(Decision.deny, ["precondition:checkout_confirmed"],
                          "handoff needs a scanner-confirmed checkout first")
    if tool == "deliver" and tr.get("state") != TransferState.in_transit:
        return Evaluation(Decision.deny, ["precondition:in_transit_confirmed"],
                          "delivery needs a scanner-confirmed courier handoff first")

    # 7. risk: similar past incidents
    if t.irreversible and s.risk_flags and s.approval_status != "approved":
        return Evaluation(Decision.pending_approval, ["similar_incident"], "resembles a past incident",
                          needs_approval_request=s.approval_status is None)

    return Evaluation(Decision.allow, hits, "all checks passed" + (" (supervisor approved)" if hits else ""))


# ------------------------------------------------------------------ state loading
async def _active_policy(db, policy_id: str) -> dict:
    p = await db.policies.find_one({"policy_id": policy_id, "active": True}, sort=[("version", -1)])
    if p is None:
        raise RuntimeError(f"no active policy {policy_id!r}; run db.seed")
    return p


async def _risk_flags(db, tool: str, args: dict, sample: dict | None, threshold: float) -> list[str]:
    text = f"{tool} {sample.get('type', '') if sample else ''} {sample.get('restriction', '') if sample else ''} " \
           f"{sample.get('description', '') if sample else ''} {args}"
    stage = await vector_stage("incidents_vector", text, limit=3, num_candidates=50)
    rows = await (await db.incidents.aggregate(
        [stage, {"$project": {"score": {"$meta": "vectorSearchScore"}}}])).to_list()
    return [f"similar_to:{r['_id']}" for r in rows if r["score"] >= threshold]


async def load_state(tool: str, args: dict, ctx: RunContext) -> GateState:
    db = agent_db()
    transfer = await db.transfers.find_one({"_id": ctx.transfer_id})
    sample_id = args.get("sample_id") or (transfer or {}).get("sample_id")
    sample = await db.samples.find_one({"_id": sample_id}) if sample_id else None
    grant = await db.grants.find_one({"_id": ctx.grant_id}) if ctx.grant_id else None
    loc_ids = {(transfer or {}).get("from"), (transfer or {}).get("to")} | _arg_locations(args)
    locations = {d["_id"]: d async for d in db.locations.find({"_id": {"$in": [x for x in loc_ids if x]}})}
    policy = await _active_policy(db, "custody-default")
    approval = await db.approvals.find_one({"transfer_id": ctx.transfer_id}, sort=[("created_at", -1)])
    has_conf = await db.ledger.find_one({"transfer_id": ctx.transfer_id, "entry_type": "confirmation"},
                                        projection={"_id": 1}) is not None
    state = GateState(
        now=utcnow(), actor=ctx.actor, on_behalf_of=ctx.on_behalf_of, transfer=transfer, grant=grant,
        sample=sample, locations=locations, policy=policy, mode=ctx.mode,
        requester=await db.identities.find_one({"_id": ctx.on_behalf_of}),
        has_confirmation=has_conf or bool((transfer or {}).get("readback_confirmed")),
        approval_status=approval["status"] if approval else None,
    )
    if ctx.mode == "normal" and spec(tool).irreversible:
        state.prior_outcomes = [d async for d in db.ledger.find(
            {"transfer_id": ctx.transfer_id, "entry_type": "outcome", "tool": tool, "status": "sent"},
            projection={"tool": 1, "status": 1})]
    if ctx.mode == "recovery":
        state.recovery_policy = await _active_policy(db, "recovery")
    elif RISK_ENABLED and spec(tool).irreversible:
        threshold = policy.get("rules", {}).get("risk_similarity_threshold", 0.85)
        state.risk_flags = await _risk_flags(db, tool, args, sample, threshold)
    return state


def _grant_snapshot(g: dict | None) -> dict | None:
    if not g:
        return None
    return {k: g.get(k) for k in ("status", "grantor", "grantee", "scope", "expires_at", "revoked_at", "revoked_by")}


def _who(user: str) -> str:
    return user.split(":", 1)[-1]


# ------------------------------------------------------------------ check / execute
async def check(*, tool: str, args: dict, reason: str, ctx: RunContext, correlation_id: str) -> GateDecision:
    """Decide, record the decision in the ledger, and open an approval / block the transfer if needed."""
    db = agent_db()
    s: GateState | None = None
    refusal = guardrails.validate_call(tool, args, reason)
    try:
        if refusal:  # malformed: refuse before any state is loaded
            ev = Evaluation(Decision.deny, [refusal.rule], refusal.detail)
            policy = await _active_policy(db, "custody-default")
        else:
            s = await load_state(tool, args, ctx)
            ev = evaluate(tool, args, s)
            policy = s.recovery_policy if ctx.mode == "recovery" else s.policy
    except Exception as e:  # noqa: BLE001 - fail closed: if the gate cannot decide, nothing runs
        s = None
        ev = Evaluation(Decision.deny, ["gate_unavailable"],
                        f"the gate could not decide ({type(e).__name__}); nothing was run")
        policy = {"policy_id": "custody-default", "version": 0}
    t = TOOLS.get(tool)
    sample_id = ((s.sample or {}).get("_id") if s else None) or args.get("sample_id")
    if refusal:  # keep what the model sent visible, but bounded and storable
        args = {str(k)[:64]: (v if isinstance(v, (bool, int, float)) or v is None else str(v)[:200])
                for k, v in args.items()}
        reason = reason[:500] if isinstance(reason, str) else str(reason)[:500]
        sample_id = sample_id if isinstance(sample_id, str) and len(sample_id) <= 64 else None

    approval_id = None
    if ev.decision == Decision.pending_approval:
        existing = await db.approvals.find_one({"transfer_id": ctx.transfer_id, "status": "pending"})
        if existing:
            approval_id = existing["_id"]
        elif ev.needs_approval_request:
            approval_id = f"APR-{uuid.uuid4().hex[:6].upper()}"
            await db.approvals.insert_one({
                "_id": approval_id, "transfer_id": ctx.transfer_id, "sample_id": sample_id,
                "requested_for_tool": tool, "reason_requested": ",".join(ev.rule_hits + (s.risk_flags if s else [])),
                "approver_role": "supervisor", "status": "pending", "decided_by": None, "decided_at": None,
                "created_at": s.now, "expires_at": s.now + APPROVAL_TTL,
            })

    verdict = {Decision.allow: "ALLOWED", Decision.deny: "DENIED", Decision.halt: "HALTED",
               Decision.pending_approval: "held for supervisor approval",
               Decision.pending_confirmation: "held for read-back confirmation"}[ev.decision]
    summary = (f"Agent {tool} {sample_id or ''} for {_who(ctx.on_behalf_of)} under {ctx.grant_id or 'no grant'} "
               f"(policy {policy['policy_id']} v{policy['version']}): {verdict}. {ev.reason}.")
    ledger_id = await ledger.append({
        "entry_type": EntryType.decision, "transfer_id": ctx.transfer_id, "sample_id": sample_id,
        "request_id": ctx.request_id, "correlation_id": correlation_id, "actor": ctx.actor,
        "on_behalf_of": ctx.on_behalf_of, "grant_id": ctx.grant_id, "grant_snapshot": _grant_snapshot(s.grant if s else None),
        "policy_id": policy["policy_id"], "policy_version": policy["version"], "tool": tool, "args": args,
        "reversible": t.reversible if t else None, "reason": reason, "decision": ev.decision,
        "rule_hits": ev.rule_hits, "risk_flags": s.risk_flags if s else [], "summary": summary, "response": {"gate_reason": ev.reason},
    })

    if ev.decision in (Decision.pending_approval, Decision.pending_confirmation) and s and s.transfer:
        blocked_reason = "approval_pending" if ev.decision == Decision.pending_approval else "readback_pending"
        current = s.transfer.get("state")
        update: dict[str, Any] = {"state": TransferState.blocked, "blocked_reason": blocked_reason,
                                  "updated_at": s.now}
        if current != TransferState.blocked:
            update["blocked_from"] = current
        if approval_id:
            update["approval_id"] = approval_id
        push = {"history": {"state": TransferState.blocked, "at": s.now}} if current != TransferState.blocked else None
        await db.transfers.update_one({"_id": ctx.transfer_id}, {"$set": update, **({"$push": push} if push else {})})

    return GateDecision(decision=ev.decision, policy_id=policy["policy_id"], policy_version=policy["version"],
                        rule_hits=ev.rule_hits, risk_flags=s.risk_flags if s else [], approval_id=approval_id,
                        ledger_id=ledger_id, correlation_id=correlation_id, reason=ev.reason)


_STATUS = {Decision.deny: "DENIED", Decision.halt: "HALTED", Decision.pending_approval: "PENDING_APPROVAL",
           Decision.pending_confirmation: "PENDING_CONFIRMATION"}
_ADVICE = {
    Decision.deny: "Do not retry this action. Explain the denial to the user.",
    Decision.halt: "Stop now. Make no further tool calls; recovery will take over.",
    Decision.pending_approval: "Wait. A supervisor approval request is open; you will be resumed when it is decided.",
    Decision.pending_confirmation: "Call request_confirmation with a one-sentence read-back, then wait.",
}
# A malformed call is not a policy refusal: the model must fix it (GUARDRAILS_SPEC "told to correct it").
CORRECTABLE = {"invalid_arguments", "unknown_tool"}
_FIX = "The call was malformed, not refused by policy. Fix what is named above and call again."


def advice(decision: Decision, rule_hits: list[str]) -> str:
    if decision == Decision.deny and CORRECTABLE & set(rule_hits):
        return _FIX
    return _ADVICE[decision]


async def execute(tool: str, args: dict[str, Any], reason: str, ctx: RunContext, executor: Executor,
                  *, correlation_id: str | None = None) -> ToolResult:
    """The only way the agent touches a lab system. Gate first; executor only on allow."""
    cid = correlation_id or f"c-{uuid.uuid4().hex[:8]}"
    d = await check(tool=tool, args=args, reason=reason, ctx=ctx, correlation_id=cid)
    if d.decision != Decision.allow:
        return ToolResult(status=_STATUS[d.decision], tool=tool, correlation_id=cid, rule_hits=d.rule_hits,
                          approval_id=d.approval_id, decision_id=d.ledger_id,
                          message=f"{_STATUS[d.decision]} ({', '.join(d.rule_hits)}): {d.reason}. {advice(d.decision, d.rule_hits)}")

    t = spec(tool)  # only reached on allow, so the tool is known
    entry_type = EntryType.outcome
    if ctx.mode == "recovery":
        entry_type = EntryType.recovery if tool == "return_to_storage" else EntryType.compensation
    base = {
        "entry_type": entry_type, "transfer_id": ctx.transfer_id, "sample_id": args.get("sample_id"),
        "request_id": ctx.request_id, "correlation_id": cid, "actor": ctx.actor, "on_behalf_of": ctx.on_behalf_of,
        "grant_id": ctx.grant_id, "policy_id": d.policy_id, "policy_version": d.policy_version, "tool": tool,
        "args": args, "reversible": t.reversible, "reason": reason, "decision": Decision.allow,
        "refs": [d.ledger_id], "request": {"tool": tool, "args": args},
    }
    try:
        response = await executor(tool, args, cid)
    except Exception as e:  # the call may or may not have landed; record it and let the loop decide
        out_id = await ledger.append({**base, "status": None, "response": {"error": str(e)[:500]},
                                      "summary": f"{tool} call failed for {ctx.transfer_id}: {str(e)[:120]}"})
        return ToolResult(status="ERROR", tool=tool, correlation_id=cid, decision_id=d.ledger_id,
                          outcome_id=out_id, message=f"ERROR calling {tool}: {e}")

    out_id = await ledger.append({
        **base, "status": "sent", "response": response,
        "summary": f"{t.system} accepted {tool} for {ctx.transfer_id}"
                   + (f"; awaiting {t.expected_scan} scan." if t.expected_scan else "."),
    })
    return ToolResult(status="OK", tool=tool, correlation_id=cid, response=response, decision_id=d.ledger_id,
                      outcome_id=out_id, expected_scan=t.expected_scan,
                      message="OK" + (f" (sent; wait for the {t.expected_scan} scan to confirm)" if t.expected_scan else ""))


# ------------------------------------------------------------------ human actions (dashboard -> agent API)
async def _unblock(db, transfer_id: str, reason: str) -> None:
    tr = await db.transfers.find_one({"_id": transfer_id})
    if not tr or tr.get("state") != TransferState.blocked or tr.get("blocked_reason") != reason:
        return
    back = tr.get("blocked_from") or TransferState.requested
    now = utcnow()
    await db.transfers.update_one(
        {"_id": transfer_id, "state": TransferState.blocked},
        {"$set": {"state": back, "blocked_reason": None, "blocked_from": None, "updated_at": now},
         "$push": {"history": {"state": back, "at": now}}})


async def confirm_readback(transfer_id: str, user_id: str, readback_text: str,
                           method: Literal["button", "voice"] = "button") -> ObjectId:
    """The tech confirmed the read-back. Writes a `confirmation` entry and unblocks the transfer."""
    db = agent_db()
    tr = await db.transfers.find_one({"_id": transfer_id})
    if tr is None:
        raise ValueError(f"unknown transfer {transfer_id}")
    if user_id != tr["requested_by"]:
        raise PermissionError("only the requesting tech can confirm the read-back")
    policy = await _active_policy(db, "custody-default")
    lid = await ledger.append({
        "entry_type": EntryType.confirmation, "transfer_id": transfer_id, "sample_id": tr["sample_id"],
        "request_id": tr.get("request_id"), "correlation_id": f"c-{uuid.uuid4().hex[:8]}", "actor": user_id,
        "on_behalf_of": user_id, "grant_id": tr["grant_id"], "policy_id": policy["policy_id"],
        "policy_version": policy["version"], "decision": Decision.allow,
        "response": {"readback_text": readback_text, "confirmed_by": user_id, "method": method},
        "summary": f"{_who(user_id)} confirmed the read-back by {method}: '{readback_text}'",
    })
    await db.transfers.update_one({"_id": transfer_id}, {"$set": {"readback_confirmed": True}})
    await _unblock(db, transfer_id, "readback_pending")
    return lid


async def decide_approval(approval_id: str, decided_by: str, approve: bool, note: str = "") -> dict:
    """A supervisor approves/denies. Checks the approver's role and clearance, records it in the ledger."""
    db = agent_db()
    apr = await db.approvals.find_one({"_id": approval_id})
    if apr is None or apr["status"] != "pending":
        raise ValueError(f"approval {approval_id} is not pending")
    approver = await db.identities.find_one({"_id": decided_by})
    sample = await db.samples.find_one({"_id": apr.get("sample_id")}) or {}
    if not approver or approver.get("role") != apr.get("approver_role", "supervisor"):
        raise PermissionError(f"{decided_by} is not a {apr.get('approver_role', 'supervisor')}")
    if sample.get("restriction") and sample["restriction"] not in approver.get("clearances", []):
        raise PermissionError(f"{decided_by} lacks {sample['restriction']} clearance")
    if decided_by == (await db.transfers.find_one({"_id": apr["transfer_id"]}) or {}).get("requested_by"):
        raise PermissionError("the requester cannot approve their own transfer")

    status = "approved" if approve else "denied"
    now = utcnow()
    await db.approvals.update_one({"_id": approval_id, "status": "pending"},
                                  {"$set": {"status": status, "decided_by": decided_by, "decided_at": now}})
    tr = await db.transfers.find_one({"_id": apr["transfer_id"]}) or {}
    policy = await _active_policy(db, "custody-default")
    await ledger.append({
        "entry_type": EntryType.decision, "transfer_id": apr["transfer_id"], "sample_id": apr.get("sample_id"),
        "request_id": tr.get("request_id"), "correlation_id": f"c-{uuid.uuid4().hex[:8]}", "actor": decided_by,
        "on_behalf_of": decided_by, "grant_id": tr.get("grant_id"), "policy_id": policy["policy_id"],
        "policy_version": policy["version"], "tool": apr["requested_for_tool"],
        "decision": Decision.allow if approve else Decision.deny, "rule_hits": ["supervisor_approval"],
        "reason": note or None, "response": {"approval_id": approval_id, "status": status},
        "summary": f"Supervisor {_who(decided_by)} {status} {apr['requested_for_tool']} of {apr.get('sample_id')} "
                   f"for {apr['transfer_id']} ({apr['reason_requested']}).",
    })
    if approve:
        await _unblock(db, apr["transfer_id"], "approval_pending")
    return {**apr, "status": status, "decided_by": decided_by, "decided_at": now}

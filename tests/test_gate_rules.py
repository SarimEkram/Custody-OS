"""Pure rule-engine tests for agent.policy_gate.evaluate (no database)."""

from datetime import datetime, timedelta, timezone

import pytest

from agent.policy_gate import GateState, evaluate

NOW = datetime(2026, 11, 14, 19, 0, tzinfo=timezone.utc)
POLICY = {"policy_id": "custody-default", "version": 3, "rules": {
    "forensic_requires_supervisor": {"restriction": "forensic",
                                     "tools": ["checkout_sample", "handoff_to_courier", "deliver"]},
    "certification_required": True, "risk_similarity_threshold": 0.85}}
RECOVERY = {"policy_id": "recovery", "version": 1,
            "rules": {"allowed_tools": ["cancel_reservation", "cancel_courier_job", "notify", "return_to_storage"]}}
LOCATIONS = {
    "FZ-A": {"_id": "FZ-A", "certifications": ["BSL-2", "FORENSIC"], "travel_minutes": {"SEQ-3": 6, "FOR-LAB": 5}},
    "FZ-B": {"_id": "FZ-B", "certifications": ["BSL-2", "FORENSIC"],
             "travel_minutes": {"SEQ-3": 4, "FOR-LAB": 3, "FZ-A": 2}},
    "SEQ-3": {"_id": "SEQ-3", "certifications": ["BSL-2"], "travel_minutes": {"FZ-A": 6, "FZ-B": 4}},
    "FOR-LAB": {"_id": "FOR-LAB", "certifications": ["FORENSIC"], "travel_minutes": {"FZ-B": 3}},
}


def grant(**kw):
    g = {"_id": "GRANT-0007", "grantor": "user:jlee", "grantee": "agent:custody-01", "status": "active",
         "expires_at": NOW + timedelta(hours=4),
         "scope": {"actions": ["lookup_sample", "reserve_station", "request_courier", "notify", "checkout_sample",
                               "handoff_to_courier", "deliver"],
                   "sample_types": ["tissue", "evidence"], "locations": list(LOCATIONS),
                   "max_restriction": "forensic"}}
    g.update(kw)
    return g


def sample(**kw):
    s = {"_id": "TS-1042", "type": "tissue", "restriction": "standard", "required_certifications": ["BSL-2"],
         "location_id": "FZ-B", "status": "stored", "max_minutes_out": 20}
    s.update(kw)
    return s


def transfer(**kw):
    t = {"_id": "TR-1", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "state": "reserved", "halt": False,
         "source": "typed", "reservation_id": "RES-1", "courier_job_id": "CJ-1", "grant_id": "GRANT-0007"}
    t.update(kw)
    return t


def state(**kw):
    s = dict(now=NOW, actor="agent:custody-01", on_behalf_of="user:jlee", transfer=transfer(), grant=grant(),
             sample=sample(), locations=LOCATIONS, policy=POLICY,
             requester={"_id": "user:jlee", "clearances": ["standard", "controlled"]})
    s.update(kw)
    return GateState(**s)


def ev(tool, st=None, args=None):
    return evaluate(tool, args or {"sample_id": "TS-1042"}, st or state())


# ---------------------------------------------------------------- happy path
@pytest.mark.parametrize("tool", ["lookup_sample", "reserve_station", "request_courier", "notify", "checkout_sample"])
def test_happy_path_allows(tool):
    assert ev(tool).decision == "allow"


def test_handoff_and_deliver_follow_scanner_state():
    assert ev("handoff_to_courier", state(transfer=transfer(state="checked_out"))).decision == "allow"
    assert ev("deliver", state(transfer=transfer(state="in_transit"))).decision == "allow"


# ---------------------------------------------------------------- 1 halt
def test_halt_flag_wins_over_everything():
    r = ev("checkout_sample", state(transfer=transfer(halt=True, halt_reason="grant revoked"), grant=None))
    assert r.decision == "halt" and r.rule_hits == ["halted"]


@pytest.mark.parametrize("st", ["revoked", "recovering", "cancelled"])
def test_inactive_transfer_halts(st):
    assert ev("reserve_station", state(transfer=transfer(state=st))).decision == "halt"


# ---------------------------------------------------------------- 1b read-back
def test_voice_irreversible_needs_readback():
    r = ev("checkout_sample", state(transfer=transfer(source="voice")))
    assert r.decision == "pending_confirmation"


def test_voice_reversible_does_not_need_readback():
    assert ev("reserve_station", state(transfer=transfer(source="voice"))).decision == "allow"


def test_voice_with_confirmation_allows():
    assert ev("checkout_sample", state(transfer=transfer(source="voice"), has_confirmation=True)).decision == "allow"


# ---------------------------------------------------------------- 2 grant
@pytest.mark.parametrize("g,hit", [
    (None, "grant_missing"),
    (grant(status="revoked"), "grant_revoked"),
    (grant(expires_at=NOW - timedelta(seconds=1)), "grant_expired"),
    (grant(grantor="user:someone"), "grant_wrong_grantor"),
    (grant(grantee="agent:other"), "grant_wrong_grantee"),
])
def test_grant_validity(g, hit):
    r = ev("reserve_station", state(grant=g))
    assert r.decision == "deny" and hit in r.rule_hits


def test_expiry_checked_even_before_ttl_deletes():
    # TTL monitor runs ~every 60s; the gate must not wait for it
    r = ev("lookup_sample", state(grant=grant(expires_at=NOW - timedelta(seconds=30))))
    assert r.rule_hits == ["grant_expired"]


# ---------------------------------------------------------------- 3 scope
def test_action_out_of_scope():
    g = grant()
    g["scope"]["actions"] = ["lookup_sample"]
    assert ev("reserve_station", state(grant=g)).rule_hits == ["action_out_of_scope"]


def test_internal_tools_need_no_scope_entry():
    g = grant()
    g["scope"]["actions"] = []
    assert ev("parse_request", state(grant=g)).decision == "allow"


def test_location_out_of_scope():
    g = grant()
    g["scope"]["locations"] = ["FZ-B"]
    assert ev("reserve_station", state(grant=g)).rule_hits == ["location_out_of_scope"]


def test_sample_type_out_of_scope():
    assert ev("lookup_sample", state(sample=sample(type="reagent"))).rule_hits == ["sample_type_out_of_scope"]


def test_restriction_above_grant():
    g = grant()
    g["scope"]["max_restriction"] = "controlled"
    assert ev("lookup_sample", state(grant=g, sample=sample(restriction="forensic"))).rule_hits == \
        ["restriction_above_grant"]


# ---------------------------------------------------------------- 4 forensic / clearance
def forensic_state(**kw):
    base = dict(sample=sample(_id="EV-2201", type="evidence", restriction="forensic",
                              required_certifications=["FORENSIC"]),
                transfer=transfer(sample_id="EV-2201", to="FOR-LAB"))
    base.update(kw)
    return state(**base)


def test_forensic_checkout_needs_supervisor():
    r = ev("checkout_sample", forensic_state(), {"sample_id": "EV-2201"})
    assert r.decision == "pending_approval" and r.needs_approval_request
    assert "forensic_requires_supervisor" in r.rule_hits


def test_forensic_reversible_steps_are_allowed():
    assert ev("reserve_station", forensic_state(), {"sample_id": "EV-2201"}).decision == "allow"


def test_forensic_pending_does_not_open_second_request():
    r = ev("checkout_sample", forensic_state(approval_status="pending"), {"sample_id": "EV-2201"})
    assert r.decision == "pending_approval" and not r.needs_approval_request


def test_forensic_approved_allows():
    assert ev("checkout_sample", forensic_state(approval_status="approved"), {"sample_id": "EV-2201"}).decision == \
        "allow"


def test_forensic_denied_denies():
    r = ev("checkout_sample", forensic_state(approval_status="denied"), {"sample_id": "EV-2201"})
    assert r.decision == "deny" and "supervisor_denied" in r.rule_hits


def test_controlled_sample_ok_for_cleared_tech():
    assert ev("checkout_sample", state(sample=sample(restriction="controlled"))).decision == "allow"


# ---------------------------------------------------------------- 5 certification
def test_forensic_sample_to_uncertified_station_denied():
    st = forensic_state(transfer=transfer(sample_id="EV-2201", to="SEQ-3"), approval_status="approved")
    r = ev("reserve_station", st, {"sample_id": "EV-2201"})
    assert r.decision == "deny" and r.rule_hits == ["destination_not_certified"]


# ---------------------------------------------------------------- 6 preconditions
def test_checkout_without_reservation_or_courier_denied():
    r = ev("checkout_sample", state(transfer=transfer(reservation_id=None, courier_job_id=None)))
    assert r.decision == "deny"
    assert {"precondition:station_reserved", "precondition:courier_assigned"} <= set(r.rule_hits)


def test_checkout_eta_over_limit_denied():
    r = ev("checkout_sample", state(sample=sample(max_minutes_out=3)))  # FZ-B -> SEQ-3 is 4 min
    assert "precondition:eta_within_limit" in r.rule_hits


def test_checkout_sample_already_out_denied():
    assert "precondition:sample_in_storage" in ev("checkout_sample", state(sample=sample(status="out"))).rule_hits


def test_handoff_before_confirmed_checkout_denied():
    assert ev("handoff_to_courier").rule_hits == ["precondition:checkout_confirmed"]


def test_deliver_before_handoff_denied():
    assert ev("deliver", state(transfer=transfer(state="checked_out"))).rule_hits == \
        ["precondition:in_transit_confirmed"]


# ---------------------------------------------------------------- 7 risk
def test_risk_flags_require_approval():
    r = ev("checkout_sample", state(risk_flags=["similar_to:INC-0004"]))
    assert r.decision == "pending_approval" and r.rule_hits == ["similar_incident"]


# ---------------------------------------------------------------- recovery
def test_recovery_allowlist_runs_without_grant():
    st = state(mode="recovery", recovery_policy=RECOVERY, grant=None, transfer=transfer(state="revoked", halt=True))
    assert ev("return_to_storage", st, {"sample_id": "TS-1042", "freezer_id": "FZ-A"}).decision == "allow"
    assert ev("cancel_reservation", st).decision == "allow"


def test_recovery_mode_blocks_normal_tools():
    st = state(mode="recovery", recovery_policy=RECOVERY)
    assert ev("checkout_sample", st).rule_hits == ["not_in_recovery_allowlist"]


def test_recovery_tools_blocked_outside_recovery():
    assert ev("return_to_storage").decision == "deny"

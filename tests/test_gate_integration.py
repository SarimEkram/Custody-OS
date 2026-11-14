"""End-to-end gate tests against the real Atlas cluster (agent_svc for the gate, admin_svc for setup/cleanup).

Run:  pytest -m integration        (needs .env and `python -m db.seed` already loaded)
"""

from datetime import timedelta

import pytest

from agent.db import admin_db, agent_db
from agent.models import utcnow
from agent.policy_gate import RunContext, confirm_readback, decide_approval, execute

pytestmark = pytest.mark.integration
PREFIX = "TR-TEST-"


class FakeLab:
    """Stands in for B's executor; records every call that actually reached a 'lab system'."""

    def __init__(self):
        self.calls: list[str] = []

    async def __call__(self, tool, args, correlation_id):
        self.calls.append(tool)
        return {"ok": True, "tool": tool, "correlation_id": correlation_id}


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    db = admin_db()  # admin-only cleanup of test rows; the app never deletes ledger rows
    for coll in ("transfers", "approvals", "ledger"):
        await db[coll].delete_many({"transfer_id" if coll != "transfers" else "_id": {"$regex": f"^{PREFIX}"}})
    await db.grants.delete_many({"_id": {"$regex": "^GRANT-TEST"}})


async def new_transfer(tid, sample_id, frm, to, source="typed", **kw):
    doc = {"_id": tid, "sample_id": sample_id, "from": frm, "to": to, "requested_by": "user:jlee",
           "grant_id": kw.pop("grant_id", "GRANT-0007"), "request_id": None, "source": source,
           "readback_confirmed": False, "blocked_reason": None, "state": "requested", "halt": False,
           "history": [], "updated_at": utcnow(), **kw}
    await agent_db().transfers.insert_one(doc)  # agent_svc may insert transfers
    return RunContext(transfer_id=tid, on_behalf_of="user:jlee", grant_id=doc["grant_id"])


async def entries(tid):
    return await agent_db().ledger.find({"transfer_id": tid}).sort("timestamp", 1).to_list()


async def test_happy_path_writes_decision_before_outcome():
    lab = FakeLab()
    ctx = await new_transfer(f"{PREFIX}1", "TS-1042", "FZ-B", "SEQ-3")
    r = await execute("reserve_station", {"station_id": "SEQ-3", "window": "14:00"}, "reserve for 2 PM", ctx, lab)
    assert r.status == "OK"
    r2 = await execute("request_courier", {"from": "FZ-B", "to": "SEQ-3"}, "need transport", ctx, lab)
    assert r2.status == "OK"
    await agent_db().transfers.update_one({"_id": ctx.transfer_id},
                                          {"$set": {"reservation_id": "RES-T", "courier_job_id": "CJ-T",
                                                    "state": "reserved"}})
    r3 = await execute("checkout_sample", {"sample_id": "TS-1042"}, "station and courier ready", ctx, lab)
    assert r3.status == "OK" and r3.expected_scan == "checkout"
    assert lab.calls == ["reserve_station", "request_courier", "checkout_sample"]

    rows = await entries(ctx.transfer_id)
    assert [(e["entry_type"], e["tool"]) for e in rows] == [
        ("decision", "reserve_station"), ("outcome", "reserve_station"),
        ("decision", "request_courier"), ("outcome", "request_courier"),
        ("decision", "checkout_sample"), ("outcome", "checkout_sample")]
    for dec, out in zip(rows[::2], rows[1::2]):
        assert out["refs"] == [dec["_id"]] and out["correlation_id"] == dec["correlation_id"]
        assert dec["policy_version"] == 3 and dec["grant_snapshot"]["status"] == "active"
    assert rows[-1]["status"] == "sent" and rows[-1]["reversible"] is False


async def test_forensic_voice_readback_then_supervisor_approval():
    lab = FakeLab()
    ctx = await new_transfer(f"{PREFIX}2", "EV-2201", "FZ-B", "FOR-LAB", source="voice",
                             reservation_id="RES-T", courier_job_id="CJ-T", state="reserved")
    r = await execute("checkout_sample", {"sample_id": "EV-2201"}, "move evidence", ctx, lab)
    assert r.status == "PENDING_CONFIRMATION" and lab.calls == []
    tr = await agent_db().transfers.find_one({"_id": ctx.transfer_id})
    assert tr["state"] == "blocked" and tr["blocked_reason"] == "readback_pending"

    await confirm_readback(ctx.transfer_id, "user:jlee", "I'll check out EV-2201 and send it to the Forensics Lab.")
    tr = await agent_db().transfers.find_one({"_id": ctx.transfer_id})
    assert tr["state"] == "reserved"

    r = await execute("checkout_sample", {"sample_id": "EV-2201"}, "move evidence", ctx, lab)
    assert r.status == "PENDING_APPROVAL" and r.approval_id and lab.calls == []  # freezer never called
    assert "forensic_requires_supervisor" in r.rule_hits

    with pytest.raises(PermissionError):
        await decide_approval(r.approval_id, "user:jlee", True)  # tech can't approve
    await decide_approval(r.approval_id, "user:mokafor", True)
    tr = await agent_db().transfers.find_one({"_id": ctx.transfer_id})
    assert tr["state"] == "reserved" and tr["blocked_reason"] is None

    r = await execute("checkout_sample", {"sample_id": "EV-2201"}, "approved by supervisor", ctx, lab)
    assert r.status == "OK" and lab.calls == ["checkout_sample"]
    types = [e["entry_type"] for e in await entries(ctx.transfer_id)]
    assert types == ["decision", "confirmation", "decision", "decision", "decision", "outcome"]


async def test_revoked_grant_is_denied_and_never_reaches_lab():
    lab = FakeLab()
    now = utcnow()
    await admin_db().grants.insert_one({
        "_id": "GRANT-TEST-1", "grantor": "user:jlee", "grantee": "agent:custody-01", "status": "active",
        "scope": {"actions": ["reserve_station"], "sample_types": ["tissue"], "locations": ["FZ-B", "SEQ-3"],
                  "max_restriction": "standard"}, "created_at": now, "expires_at": now + timedelta(hours=1)})
    ctx = await new_transfer(f"{PREFIX}3", "TS-1042", "FZ-B", "SEQ-3", grant_id="GRANT-TEST-1")
    assert (await execute("reserve_station", {"station_id": "SEQ-3"}, "r", ctx, lab)).status == "OK"
    await admin_db().grants.update_one({"_id": "GRANT-TEST-1"},
                                       {"$set": {"status": "revoked", "revoked_by": "user:jlee"}})
    r = await execute("reserve_station", {"station_id": "SEQ-3"}, "r", ctx, lab)
    assert r.status == "DENIED" and r.rule_hits == ["grant_revoked"] and lab.calls == ["reserve_station"]


async def test_halt_flag_stops_next_call():
    lab = FakeLab()
    ctx = await new_transfer(f"{PREFIX}4", "TS-1042", "FZ-B", "SEQ-3", halt=True, halt_reason="Stop pressed")
    r = await execute("lookup_sample", {"sample_id": "TS-1042"}, "check location", ctx, lab)
    assert r.status == "HALTED" and lab.calls == []
    row = (await entries(ctx.transfer_id))[-1]
    assert row["decision"] == "halt" and row["entry_type"] == "decision"


async def test_agent_db_user_cannot_rewrite_its_decisions():
    from pymongo.errors import OperationFailure
    ctx = await new_transfer(f"{PREFIX}5", "TS-1042", "FZ-B", "SEQ-3")
    r = await execute("lookup_sample", {"sample_id": "TS-1042"}, "x", ctx, FakeLab())
    with pytest.raises(OperationFailure) as e:
        await agent_db()["ledger"].update_one({"_id": r.decision_id}, {"$set": {"decision": "deny"}})
    assert e.value.code == 13

"""Watchers + verified scans against the real Atlas cluster.

Run:  pytest -m integration
"""

import asyncio
from datetime import timedelta

import pytest

from agent.control import press_stop, revoke_grant
from agent.custody import apply_verified_scan, wait_for_scan
from agent.db import admin_db, agent_db, scanner_db
from agent.models import utcnow
from agent.policy_gate import RunContext, execute
from agent.signing import sign_scan
from agent.watchers import Watchers

pytestmark = pytest.mark.integration
PREFIX = "TR-TESTW-"


class FakeLab:
    def __init__(self):
        self.calls = []

    async def __call__(self, tool, args, cid):
        self.calls.append(tool)
        return {"ok": True}


@pytest.fixture
async def watchers():
    halted, scans, approvals = [], [], []

    async def on_halt(tr, reason, by):
        halted.append((tr["_id"], reason, by))

    async def on_scan(scan):
        if scan["sample_id"] == "TS-1042" and str(scan.get("correlation_id", "")).startswith("c-testw"):
            scans.append(await apply_verified_scan(scan))

    w = Watchers(on_halt=on_halt, on_scan=on_scan, on_approval=lambda a: approvals.append(a) or asyncio.sleep(0))
    task = asyncio.create_task(w.run())
    await asyncio.sleep(3)  # let the change streams open
    yield {"halted": halted, "scans": scans}
    task.cancel()
    db = admin_db()  # admin-only cleanup of test rows
    for coll in ("transfers", "ledger", "approvals"):
        await db[coll].delete_many({"transfer_id" if coll != "transfers" else "_id": {"$regex": f"^{PREFIX}"}})
    await db.custody_events.delete_many({"correlation_id": {"$regex": "^c-testw"}})
    await db.grants.delete_many({"_id": {"$regex": "^GRANT-TESTW"}})
    await db.control.delete_many({"issued_by": "user:testw"})
    await db.samples.update_one({"_id": "TS-1042"}, {"$set": {"location_id": "FZ-B", "custodian": "location:FZ-B",
                                                              "status": "stored", "checked_out_at": None}})


async def until(pred, timeout=15):
    for _ in range(timeout * 4):
        if await pred() if asyncio.iscoroutinefunction(pred) else pred():
            return True
        await asyncio.sleep(0.25)
    return False


async def make_transfer(tid, grant_id="GRANT-0007", **kw):
    await agent_db().transfers.insert_one({
        "_id": tid, "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "requested_by": "user:jlee",
        "grant_id": grant_id, "request_id": None, "source": "typed", "readback_confirmed": False,
        "blocked_reason": None, "state": "reserved", "halt": False, "history": [], "updated_at": utcnow(),
        "reservation_id": "RES-T", "courier_job_id": "CJ-T", **kw})
    return RunContext(transfer_id=tid, on_behalf_of="user:jlee", grant_id=grant_id)


async def test_revoke_halts_transfer_and_logs_it(watchers):
    now = utcnow()
    await admin_db().grants.insert_one({
        "_id": "GRANT-TESTW-1", "grantor": "user:jlee", "grantee": "agent:custody-01", "status": "active",
        "scope": {"actions": ["lookup_sample"], "sample_types": ["tissue"], "locations": ["FZ-B", "SEQ-3"],
                  "max_restriction": "standard"}, "created_at": now, "expires_at": now + timedelta(hours=1)})
    ctx = await make_transfer(f"{PREFIX}1", grant_id="GRANT-TESTW-1")
    await revoke_grant("GRANT-TESTW-1", "user:jlee", "tech changed plans")
    assert await until(lambda: any(h[0] == ctx.transfer_id for h in watchers["halted"]))
    tr = await agent_db().transfers.find_one({"_id": ctx.transfer_id})
    assert tr["halt"] is True and tr["state"] == "revoked"
    row = await agent_db().ledger.find_one({"transfer_id": ctx.transfer_id, "decision": "halt"})
    assert row["rule_hits"] == ["grant_revoked"] and row["actor"] == "system:watcher"
    r = await execute("lookup_sample", {"sample_id": "TS-1042"}, "x", ctx, FakeLab())
    assert r.status == "HALTED"


async def test_stop_button_halts_one_transfer(watchers):
    ctx = await make_transfer(f"{PREFIX}2")
    await press_stop("user:testw", ctx.transfer_id)
    assert await until(lambda: any(h[0] == ctx.transfer_id for h in watchers["halted"]))
    row = await agent_db().ledger.find_one({"transfer_id": ctx.transfer_id, "decision": "halt"})
    assert row["rule_hits"] == ["stop_switch"]


async def test_signed_scan_advances_state_in_one_transaction(watchers):
    lab = FakeLab()
    ctx = await make_transfer(f"{PREFIX}3")
    r = await execute("checkout_sample", {"sample_id": "TS-1042"}, "ready", ctx, lab,
                      correlation_id="c-testw-ok")
    assert r.status == "OK"
    scan = {"scanner_id": "scanner:reader-fzb", "type": "checkout", "sample_id": "TS-1042", "location_id": "FZ-B",
            "from_custodian": "location:FZ-B", "to_custodian": "courier:CJ-T", "correlation_id": "c-testw-ok",
            "scanned_at": utcnow().replace(microsecond=0)}
    scan["signature"] = sign_scan(scan)
    waiter = asyncio.create_task(wait_for_scan("c-testw-ok", timeout=15))
    await scanner_db().custody_events.insert_one(scan)  # only the scanner user may do this
    assert (await waiter)["correlation_id"] == "c-testw-ok"
    assert await until(lambda: "checked_out" in watchers["scans"])

    tr = await agent_db().transfers.find_one({"_id": ctx.transfer_id})
    sample = await agent_db().samples.find_one({"_id": "TS-1042"})
    ver = await agent_db().ledger.find_one({"transfer_id": ctx.transfer_id, "entry_type": "verification"})
    assert tr["state"] == "checked_out" and sample["status"] == "out" and sample["checked_out_at"]
    assert ver["status"] == "confirmed" and ver["refs"] == [r.outcome_id]


async def test_forged_scan_is_a_violation(watchers):
    ctx = await make_transfer(f"{PREFIX}4")
    await execute("checkout_sample", {"sample_id": "TS-1042"}, "ready", ctx, FakeLab(), correlation_id="c-testw-bad")
    scan = {"scanner_id": "scanner:reader-fzb", "type": "checkout", "sample_id": "TS-1042", "location_id": "FZ-B",
            "correlation_id": "c-testw-bad", "scanned_at": utcnow(), "signature": "hmac-sha256:" + "0" * 64}
    await scanner_db().custody_events.insert_one(scan)
    assert await until(lambda: len(watchers["scans"]) >= 1)
    tr = await agent_db().transfers.find_one({"_id": ctx.transfer_id})
    v = await agent_db().ledger.find_one({"transfer_id": ctx.transfer_id, "entry_type": "violation"})
    assert tr["state"] == "reserved" and v["status"] == "mismatch" and "bad signature" in v["response"]["detail"]


async def test_no_scan_times_out_as_unconfirmed():
    assert await wait_for_scan("c-testw-never", timeout=2) is None

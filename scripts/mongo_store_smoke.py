"""Live check of the Mongo-backed stores.

Writes only to a throwaway database (dropped at the end). Against the real `custody` database
it only reads, as agent_svc, to prove the seeded data loads under the agent's privileges.

    .venv/bin/python scripts/mongo_store_smoke.py
"""

import asyncio
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")
from agent.db import client as mongo_client  # noqa: E402
from agent.db import database_name  # noqa: E402
from agent.mongo_stores import (  # noqa: E402
    MongoAudio,
    MongoCustodyStore,
    MongoRequests,
    find_grant,
    load_catalog,
    load_courier_travel,
    sha256,
    start_scan_feed,
)
from agent.transfers import StateConflict  # noqa: E402
from agent.verifier import ScanBus  # noqa: E402
from scripts._env import load_env  # noqa: E402
from services.scanner.store import MongoEventStore  # noqa: E402

failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        failures.append(name)


async def real_database_read_only() -> None:
    c = mongo_client(os.environ["MONGODB_URI_AGENT"])
    db = c[database_name()]
    try:
        cat = await load_catalog(db)
        check(
            "agent_svc loads the seeded catalog",
            len(cat.samples) >= 3 and "FZ-B" in cat.locations,
            f"{len(cat.samples)} samples, {len(cat.locations)} locations",
        )
        travel = await load_courier_travel(db)
        check(
            "travel table from locations.travel_minutes", len(travel) >= 5, f"{len(travel)} pairs"
        )
        grant = await find_grant(db, "agent:custody-01")("user:jlee")
        check("jlee has an active, unexpired grant", grant is not None, str(grant))
        nxt = await MongoCustodyStore(c, db).next_transfer_id()
        check("next transfer id continues the seeded numbering", nxt > "TR-0001", nxt)
    finally:
        await c.close()


async def scratch_round_trips() -> None:
    c = mongo_client(os.environ["MONGODB_URI_ADMIN"])
    name = f"custody_scratch_b_{secrets.token_hex(3)}"
    db = c[name]
    try:
        now = datetime.now(UTC)
        await db.samples.insert_one({"_id": "TS-1", "status": "stored", "location_id": "FZ-B"})
        await db.transfers.insert_one({"_id": "TR-0001", "state": "reserved", "history": []})
        store = MongoCustodyStore(c, db)

        async def append(entry, session=None):
            await db.scratch_audit.insert_one(entry, session=session)

        await store.block("TR-0001", "approval_pending")
        t = await store.get_transfer("TR-0001")
        check(
            "block remembers where it paused",
            t["state"] == "blocked" and t["blocked_from"] == "reserved",
        )
        await store.unblock("TR-0001")
        check("unblock restores it", (await store.get_transfer("TR-0001"))["state"] == "reserved")

        scan = {"_id": "scan-1", "sample_id": "TS-1", "type": "checkout", "location_id": "FZ-B",
                "to_custodian": "courier:CJ-1", "scanned_at": now}  # fmt: skip
        await store.apply_verified_scan(
            "TR-0001", "reserved", "checked_out", scan, {"k": "v1"}, append
        )
        t, s = await store.get_transfer("TR-0001"), await store.get_sample("TS-1")
        check("verified scan commits transfer + sample + ledger together",
              t["state"] == "checked_out" and s["status"] == "out"
              and await db.scratch_audit.count_documents({"k": "v1"}) == 1)  # fmt: skip
        check("datetimes come back timezone-aware", s["checked_out_at"].tzinfo is not None)

        try:
            await store.apply_verified_scan(
                "TR-0001", "reserved", "in_transit", scan, {"k": "v2"}, append
            )
            check("a stale expected state is refused", False)
        except StateConflict:
            check("a stale expected state is refused and rolls back",
                  await db.scratch_audit.count_documents({"k": "v2"}) == 0
                  and (await store.get_transfer("TR-0001"))["state"] == "checked_out")  # fmt: skip

        reqs = MongoRequests(db)
        await db.voice_requests.insert_one({"_id": "VR-0001"})
        rid = await reqs.next_id()
        await reqs.insert({"_id": rid, "transcript": "x"})
        check("request ids continue after existing ones", rid == "VR-0002", rid)

        audio = MongoAudio(db)
        clip = secrets.token_bytes(2048)
        file_id = await audio.put(clip, "clip.webm")
        back = await audio.get(file_id)
        check(
            "GridFS round-trips audio byte for byte", back == clip and sha256(back) == sha256(clip)
        )

        await db.grants.insert_many([
            {"_id": "G-ok", "grantor": "user:a", "grantee": "agent:x", "status": "active",
             "expires_at": now + timedelta(hours=1)},
            {"_id": "G-old", "grantor": "user:b", "grantee": "agent:x", "status": "active",
             "expires_at": now - timedelta(minutes=1)},
        ])  # fmt: skip
        lookup = find_grant(db, "agent:x")
        check("an unexpired grant is found", await lookup("user:a") == "G-ok")
        check("an expired-but-not-yet-deleted grant is not", await lookup("user:b") is None)

        bus = ScanBus()
        feed = asyncio.create_task(start_scan_feed(db, bus))
        await asyncio.sleep(1.5)  # let the change stream open
        await MongoEventStore(db).insert({"correlation_id": "c-live", "type": "checkout"})
        got = await bus.wait_for("c-live", 6.0)
        feed.cancel()
        check(
            "a custody_event reaches the verifier's bus through the change stream", got is not None
        )
    finally:
        await c.drop_database(name)
        await c.close()


async def main() -> int:
    load_env()
    await real_database_read_only()
    await scratch_round_trips()
    print(f"\n{'ALL PASS' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


sys.exit(asyncio.run(main()))

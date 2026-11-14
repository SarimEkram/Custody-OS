"""Verified custody handoffs: a scan advances a transfer, atomically (CLAUDE.md rule 3).

`apply_verified_scan()` runs one multi-document transaction:
    samples (location/custodian/clock) + transfers (state, optimistic guard) + ledger (verification entry)
so the three can never disagree. Only a scanner event can move a transfer into
checked_out / in_transit / received / recovered.
"""

from __future__ import annotations

from datetime import timezone

from agent import ledger
from agent.config import settings
from agent.db import agent_client, agent_db
from agent.models import Decision, EntryType, OutcomeStatus, TransferState, utcnow
from agent.signing import verify_scan

NEXT_STATE = {"checkout": TransferState.checked_out, "handoff": TransferState.in_transit,
              "intake": TransferState.received, "checkin": TransferState.recovered}
EXPECTED_FROM = {"checkout": {TransferState.reserved}, "handoff": {TransferState.checked_out},
                 "intake": {TransferState.in_transit},
                 "checkin": {TransferState.recovering, TransferState.revoked, TransferState.checked_out,
                             TransferState.in_transit}}
SCAN_TOOL = {"checkout": "checkout_sample", "handoff": "handoff_to_courier", "intake": "deliver",
             "checkin": "return_to_storage"}


class StateConflict(Exception):
    pass


def _minutes(a, b) -> float:
    a = a if a.tzinfo else a.replace(tzinfo=timezone.utc)
    b = b if b.tzinfo else b.replace(tzinfo=timezone.utc)
    return round((b - a).total_seconds() / 60, 2)


async def _policy(db) -> dict:
    return await db.policies.find_one({"policy_id": "custody-default", "active": True}, sort=[("version", -1)])


def _base(transfer: dict, scan: dict, policy: dict) -> dict:
    return {"transfer_id": transfer["_id"], "sample_id": scan["sample_id"], "request_id": transfer.get("request_id"),
            "correlation_id": scan.get("correlation_id") or f"scan-{scan['_id']}", "actor": "system:verifier",
            "on_behalf_of": transfer["requested_by"], "grant_id": transfer.get("grant_id"),
            "policy_id": policy["policy_id"], "policy_version": policy["version"], "tool": SCAN_TOOL.get(scan["type"]),
            "decision": Decision.allow}


async def record_unverified(transfer: dict, scan: dict | None, status: OutcomeStatus, detail: str,
                            correlation_id: str | None = None) -> None:
    """Write a `mismatch` (wrong sample/location, bad signature) or `unconfirmed` (timeout) verification entry."""
    db = agent_db()
    policy = await _policy(db)
    scan = scan or {"_id": "none", "sample_id": transfer["sample_id"], "type": "", "correlation_id": correlation_id}
    entry_type = EntryType.violation if status == OutcomeStatus.mismatch else EntryType.verification
    await ledger.append({**_base(transfer, scan, policy), "entry_type": entry_type, "status": status,
                         "response": {"detail": detail, "scan_id": str(scan.get("_id"))},
                         "summary": f"Verification {status} for {transfer['_id']} ({scan['sample_id']}): {detail}"})


async def apply_verified_scan(scan: dict, expected_location: str | None = None) -> str:
    """Match a scan to its transfer and advance state inside one transaction. Returns the new state.

    Raises StateConflict if the transfer moved underneath us. Writes a violation entry on mismatch.
    """
    db = agent_db()
    cid = scan.get("correlation_id")
    outcome = await db.ledger.find_one({"correlation_id": cid, "entry_type": {"$in": ["outcome", "recovery"]}}) \
        if cid else None
    transfer = await db.transfers.find_one({"_id": outcome["transfer_id"]}) if outcome else \
        await db.transfers.find_one({"sample_id": scan["sample_id"],
                                     "state": {"$nin": ["received", "recovered", "cancelled"]}})
    if transfer is None:
        raise LookupError(f"no active transfer for scan {scan.get('_id')} ({scan['sample_id']})")

    problems = []
    if not verify_scan(scan):
        problems.append("bad signature")
    if scan["sample_id"] != transfer["sample_id"]:
        problems.append(f"sample {scan['sample_id']} != {transfer['sample_id']}")
    if expected_location and scan["location_id"] != expected_location:
        problems.append(f"location {scan['location_id']} != {expected_location}")
    if scan["type"] not in NEXT_STATE:
        problems.append(f"unexpected scan type {scan['type']}")
    elif transfer["state"] not in EXPECTED_FROM[scan["type"]]:
        problems.append(f"{scan['type']} scan while transfer is {transfer['state']}")
    if problems:
        await record_unverified(transfer, scan, OutcomeStatus.mismatch, "; ".join(problems))
        return transfer["state"]

    next_state = NEXT_STATE[scan["type"]]
    policy = await _policy(db)
    sample = await db.samples.find_one({"_id": scan["sample_id"]})
    now = utcnow()
    sample_set: dict = {"location_id": scan["location_id"],
                        "custodian": scan.get("to_custodian") or f"location:{scan['location_id']}"}
    exposure = None
    if scan["type"] == "checkout":
        sample_set |= {"status": "out", "checked_out_at": scan["scanned_at"]}
    elif scan["type"] in ("checkin", "intake"):
        if sample.get("checked_out_at"):
            mins = _minutes(sample["checked_out_at"], scan["scanned_at"])
            exposure = {"minutes_out": mins, "limit_minutes": sample["max_minutes_out"],
                        "exceeded": mins > sample["max_minutes_out"]}
        sample_set |= {"checked_out_at": None,
                       "status": "compromised" if exposure and exposure["exceeded"] else "stored"}

    client = agent_client()
    async with client.start_session() as s:
        async with await s.start_transaction():
            await db.samples.update_one({"_id": scan["sample_id"]}, {"$set": sample_set}, session=s)
            res = await db.transfers.update_one(
                {"_id": transfer["_id"], "state": transfer["state"]},  # optimistic guard
                {"$set": {"state": next_state, "updated_at": now},
                 "$push": {"history": {"state": next_state, "at": now, "scan_id": scan["_id"]}}},
                session=s)
            if res.modified_count != 1:
                raise StateConflict(transfer["_id"])
            base = _base(transfer, scan, policy)
            await ledger.append({
                **base, "entry_type": EntryType.verification, "status": OutcomeStatus.confirmed,
                "refs": [outcome["_id"]] if outcome else [],
                "response": {"scan_id": str(scan["_id"]), "scanner_id": scan["scanner_id"],
                             "location_id": scan["location_id"], "type": scan["type"]},
                "summary": f"Scanner {scan['scanner_id']} confirmed {scan['type']} of {scan['sample_id']} at "
                           f"{scan['location_id']}; {transfer['_id']} is now {next_state}.",
            }, session=s)
            if exposure:
                await ledger.append({
                    **base, "entry_type": EntryType.violation if exposure["exceeded"] else EntryType.exposure,
                    "response": exposure, "tool": None,
                    "summary": f"{scan['sample_id']} was out of storage {exposure['minutes_out']} min "
                               f"(limit {exposure['limit_minutes']}): "
                               + ("EXCURSION, sample compromised." if exposure["exceeded"] else "within limit."),
                }, session=s)
    return next_state


async def wait_for_scan(correlation_id: str, timeout: float | None = None) -> dict | None:
    """Wait for the scanner's event for this correlation_id (change stream). None on timeout -> `unconfirmed`."""
    import asyncio
    db = agent_db()
    existing = await db.custody_events.find_one({"correlation_id": correlation_id})
    if existing:
        return existing
    pipeline = [{"$match": {"operationType": "insert", "fullDocument.correlation_id": correlation_id}}]
    try:
        async with asyncio.timeout(timeout or settings.verifier_timeout_seconds):
            async with await db.custody_events.watch(pipeline) as stream:
                existing = await db.custody_events.find_one({"correlation_id": correlation_id})
                if existing:  # arrived between the first check and opening the stream
                    return existing
                async for ch in stream:
                    return ch["fullDocument"]
    except TimeoutError:
        return None
    return None

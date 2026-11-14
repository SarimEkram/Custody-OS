"""Demo seed data (docs/DEMO.md "Seed data"). Idempotent; run as admin_svc.

Usage:  python -m db.seed            upsert reference data (people, grant, samples, locations, policies, incidents)
        python -m db.seed --reset    also wipe demo-mutable collections and re-add the "earlier today" history

Person C owns extending this (more history, voice requests); keep it passing the validators in db/validators/.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta

import gridfs
from pymongo import MongoClient

from agent import ledger
from agent.config import settings
from agent.db import admin_db
from agent.models import utcnow

NOW = utcnow()

IDENTITIES = [
    {"_id": "user:jlee", "kind": "human", "name": "Jordan Lee", "role": "lab_tech",
     "clearances": ["standard", "controlled"], "supervisor_id": "user:mokafor"},
    {"_id": "user:mokafor", "kind": "human", "name": "M. Okafor", "role": "supervisor",
     "clearances": ["standard", "controlled", "forensic"]},
    {"_id": "agent:custody-01", "kind": "agent", "name": "Custody Agent", "model": settings.llm_model},
    # reader id suffix -> the real location id (not suffix.upper(): that gave "FZA", "SEQ3", ...)
    *({"_id": f"scanner:reader-{suffix}", "kind": "scanner", "location_id": loc}
      for suffix, loc in (("fza", "FZ-A"), ("fzb", "FZ-B"), ("corr1", "CORR-1"),
                          ("seq3", "SEQ-3"), ("forlab", "FOR-LAB"))),
    {"_id": "scanner:courier", "kind": "scanner", "location_id": "CORR-1"},
]

# travel_minutes is symmetric; CORR-1 is the corridor every route passes through.
# FZ-A <-> SEQ-3 is 2 min so TS-1077 (3-min limit) passes the checkout ETA rule in beat 3.
_TRAVEL = {("FZ-A", "FZ-B"): 2, ("FZ-A", "SEQ-3"): 2, ("FZ-A", "FOR-LAB"): 5, ("FZ-B", "SEQ-3"): 4,
           ("FZ-B", "FOR-LAB"): 3, ("SEQ-3", "FOR-LAB"): 6, ("CORR-1", "FZ-A"): 1, ("CORR-1", "FZ-B"): 1,
           ("CORR-1", "SEQ-3"): 1, ("CORR-1", "FOR-LAB"): 2}


def _travel(loc: str) -> dict[str, int]:
    return {b if a == loc else a: m for (a, b), m in _TRAVEL.items() if loc in (a, b)}


LOCATIONS = [
    {"_id": "FZ-A", "name": "Freezer A", "kind": "freezer", "certifications": ["BSL-2", "FORENSIC"],
     "map": {"x": 120, "y": 120}},
    {"_id": "FZ-B", "name": "Freezer B", "kind": "freezer", "certifications": ["BSL-2", "FORENSIC"],
     "map": {"x": 120, "y": 320}},
    {"_id": "CORR-1", "name": "Corridor", "kind": "corridor", "certifications": [], "map": {"x": 380, "y": 220}},
    {"_id": "SEQ-3", "name": "Sequencing Station 3", "kind": "station", "certifications": ["BSL-2"],
     "map": {"x": 640, "y": 120}},
    {"_id": "FOR-LAB", "name": "Forensics Lab", "kind": "lab", "certifications": ["FORENSIC"],
     "map": {"x": 640, "y": 320}},
]
for _loc in LOCATIONS:
    _loc["travel_minutes"] = _travel(_loc["_id"])

SAMPLES = [
    {"_id": "TS-1042", "type": "tissue", "description": "Liver tissue biopsy, study ONC-7", "restriction": "standard",
     "required_certifications": ["BSL-2"], "location_id": "FZ-B", "custodian": "location:FZ-B", "status": "stored",
     "max_minutes_out": 20, "checked_out_at": None, "case_ref": None},
    {"_id": "EV-2201", "type": "evidence", "description": "DNA swab, case 24-1187", "restriction": "forensic",
     "required_certifications": ["FORENSIC"], "location_id": "FZ-B", "custodian": "location:FZ-B",
     "status": "stored", "max_minutes_out": 20, "checked_out_at": None, "case_ref": "24-1187"},
    {"_id": "TS-1077", "type": "tissue", "description": "Tumour tissue, temperature-sensitive",
     "restriction": "controlled", "required_certifications": ["BSL-2"], "location_id": "FZ-A",
     "custodian": "location:FZ-A", "status": "stored", "max_minutes_out": 3, "checked_out_at": None,
     "case_ref": None},
]

ALL_ACTIONS = ["lookup_sample", "reserve_station", "request_courier", "notify", "checkout_sample",
               "handoff_to_courier", "deliver"]
GRANTS = [
    {"_id": "GRANT-0007", "grantor": "user:jlee", "grantee": "agent:custody-01", "status": "active",
     "scope": {"actions": ALL_ACTIONS, "sample_types": ["tissue", "evidence"],
               "locations": ["FZ-A", "FZ-B", "SEQ-3", "FOR-LAB", "CORR-1"], "max_restriction": "forensic"},
     "created_at": NOW, "expires_at": NOW + timedelta(hours=6), "revoked_at": None, "revoked_by": None,
     "revoke_reason": None},
]

_RULES_V3 = {
    "forensic_requires_supervisor": {"restriction": "forensic",
                                     "tools": ["checkout_sample", "handoff_to_courier", "deliver"]},
    "certification_required": True,
    "checkout_preconditions": ["station_reserved", "courier_assigned", "eta_within_limit"],
    "handoff_scan_window_seconds": 120,
    "risk_similarity_threshold": 0.85,
}
POLICIES = [
    {"_id": "custody-default@2", "policy_id": "custody-default", "version": 2, "active": False,
     "rules": {**_RULES_V3, "checkout_preconditions": ["station_reserved"]},
     "created_by": "user:mokafor", "created_at": NOW - timedelta(days=30)},
    {"_id": "custody-default@3", "policy_id": "custody-default", "version": 3, "active": True, "rules": _RULES_V3,
     "created_by": "user:mokafor", "created_at": NOW - timedelta(days=2)},
    {"_id": "recovery@1", "policy_id": "recovery", "version": 1, "active": True,
     "rules": {"allowed_tools": ["cancel_reservation", "cancel_courier_job", "notify", "return_to_storage"],
               "requires_valid_grant": False},
     "created_by": "user:mokafor", "created_at": NOW - timedelta(days=30)},
]

INCIDENTS = [
    ("INC-0001", "Forensic DNA swab moved to an uncertified station during shift change; chain of custody "
                 "challenged in court.", ["forensic", "certification", "shift_change"], "high"),
    ("INC-0002", "Tissue sample left in the corridor for 35 minutes after a courier was reassigned; sample "
                 "thawed and was discarded.", ["exposure", "courier"], "high"),
    ("INC-0003", "Evidence checked out of the freezer without a supervisor signature on the transfer form.",
     ["forensic", "approval"], "high"),
    ("INC-0004", "Two samples with similar IDs swapped at intake because the barcode was not scanned.",
     ["scan", "intake", "mix_up"], "medium"),
    ("INC-0005", "Transfer started under an expired authorization after the requesting tech went off shift.",
     ["authorization", "expiry"], "medium"),
    ("INC-0006", "Courier delivered a controlled sample to the wrong station; rerouted with 2 minutes of "
                 "exposure margin left.", ["courier", "reroute", "exposure"], "medium"),
    ("INC-0007", "Freezer B door alarm during checkout; sample temperature log shows a 4-minute excursion.",
     ["freezer", "exposure"], "low"),
]

MUTABLE = ["transfers", "ledger", "custody_events", "approvals", "reservations", "control", "voice_requests"]


def upsert_reference(db) -> None:
    for coll, docs in (("identities", IDENTITIES), ("locations", LOCATIONS), ("samples", SAMPLES),
                       ("grants", GRANTS), ("policies", POLICIES)):
        for d in docs:
            db[coll].replace_one({"_id": d["_id"]}, d, upsert=True)
        print(f"seeded   {coll}: {len(docs)}")
    for _id, summary, tags, sev in INCIDENTS:
        db.incidents.replace_one({"_id": _id}, {"_id": _id, "summary": summary, "tags": tags, "severity": sev},
                                 upsert=True)
    print(f"seeded   incidents: {len(INCIDENTS)}")


def reset_mutable(db) -> None:
    for coll in MUTABLE:
        n = db[coll].delete_many({}).deleted_count  # admin-only reset; the app never deletes ledger rows
        print(f"reset    {coll}: {n} removed")
    bucket = gridfs.GridFSBucket(db, bucket_name="voice_audio")
    for f in db["voice_audio.files"].find({}, {"_id": 1}):
        bucket.delete(f["_id"])


# One finished transfer, shaped exactly like a real run: decision -> outcome (sent) -> for physical steps a
# scanner-signed custody_event and a verification row. (Decisions alone render as "Running" forever in the
# dashboard, and an irreversible outcome with no scan shows up as a false gap in the auditor.)
HISTORY_STEPS = [
    # tool, reversible, summary of the decision, scan (type, location, scanner, to_custodian, text) or None
    ("reserve_station", True, "Agent reserve_station TS-1042 for jlee under GRANT-0007 (policy custody-default v3): "
                              "ALLOWED. all checks passed.", None),
    ("request_courier", True, "Agent request_courier TS-1042 for jlee under GRANT-0007 (policy custody-default v3): "
                              "ALLOWED. all checks passed.", None),
    ("checkout_sample", False, "Agent checkout_sample TS-1042 for jlee under GRANT-0007 (policy custody-default v3): "
                               "ALLOWED. all checks passed.",
     ("checkout", "FZ-B", "scanner:reader-fzb", "courier:CJ-HIST", "Scanner confirmed TS-1042 left FZ-B.")),
    ("handoff_to_courier", False, "Agent handoff_to_courier TS-1042 for jlee under GRANT-0007 (policy "
                                  "custody-default v3): ALLOWED. all checks passed.",
     ("handoff", "CORR-1", "scanner:courier", "courier:CJ-HIST", "Scanner confirmed courier picked up TS-1042.")),
    ("deliver", False, "Agent deliver TS-1042 for jlee under GRANT-0007 (policy custody-default v3): ALLOWED. "
                       "all checks passed.",
     ("intake", "SEQ-3", "scanner:reader-seq3", "location:SEQ-3", "Scanner confirmed SEQ-3 received TS-1042.")),
]
DENIED_TEXT = "Move EV-2201 to Sequencing Station 3."


async def seed_history() -> None:
    """A finished transfer (TR-0001) and one denied request (TR-0002), so the auditor view isn't empty."""
    from agent.signing import sign_scan

    db = admin_db()
    t0 = NOW - timedelta(hours=3)
    base = {"actor": "agent:custody-01", "on_behalf_of": "user:jlee", "grant_id": "GRANT-0007",
            "policy_id": "custody-default", "policy_version": 3}
    await db.voice_requests.insert_many([
        {"_id": "VR-0001", "source": "typed", "speaker_id": "user:jlee", "audio_file_id": None,
         "audio_sha256": None, "stt": None, "transcript": "Move TS-1042 from Freezer B to Sequencing Station 3.",
         "words": [], "low_confidence_terms": [],
         "parsed": {"sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3"}, "transfer_id": "TR-0001",
         "created_at": t0},
        {"_id": "VR-0002", "source": "typed", "speaker_id": "user:jlee", "audio_file_id": None,
         "audio_sha256": None, "stt": None, "transcript": DENIED_TEXT, "words": [], "low_confidence_terms": [],
         "parsed": {"sample_id": "EV-2201", "from": "FZ-B", "to": "SEQ-3"}, "transfer_id": "TR-0002",
         "created_at": t0 + timedelta(minutes=39)}])
    await db.transfers.insert_many([
        {"_id": "TR-0001", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "requested_by": "user:jlee",
         "grant_id": "GRANT-0007", "request_id": "VR-0001", "source": "typed", "state": "received", "halt": False,
         "blocked_reason": None, "request_text": "Move TS-1042 from Freezer B to Sequencing Station 3.",
         "history": [{"state": st, "at": t0 + timedelta(minutes=i)} for i, st in
                     enumerate(["requested", "reserved", "checked_out", "in_transit", "received"])],
         "updated_at": t0 + timedelta(minutes=5)},
        # owns the denial below, so the id is taken and a new real transfer can never reuse it
        {"_id": "TR-0002", "sample_id": "EV-2201", "from": "FZ-B", "to": "SEQ-3", "requested_by": "user:jlee",
         "grant_id": "GRANT-0007", "request_id": "VR-0002", "source": "typed", "state": "cancelled", "halt": False,
         "blocked_reason": None, "request_text": DENIED_TEXT,
         "history": [{"state": "requested", "at": t0 + timedelta(minutes=39)},
                     {"state": "cancelled", "at": t0 + timedelta(minutes=40)}],
         "updated_at": t0 + timedelta(minutes=40)}])

    minute = 0

    def at() -> datetime:
        nonlocal minute
        minute += 1
        return t0 + timedelta(minutes=minute, seconds=5 * minute)

    tr1 = {"transfer_id": "TR-0001", "sample_id": "TS-1042", "request_id": "VR-0001"}
    await ledger.append({**base, **tr1, "timestamp": t0, "entry_type": "intake", "actor": "user:jlee",
                         "correlation_id": "c-hist-intake", "decision": "allow", "policy_id": "intake",
                         "policy_version": 1, "response": {"transcript": "Move TS-1042 from Freezer B to "
                         "Sequencing Station 3."}, "summary": "jlee requested by typing: 'Move TS-1042 from "
                         "Freezer B to Sequencing Station 3.'"}, db=db)
    for tool, reversible, decision_text, scan in HISTORY_STEPS:
        cid = f"c-hist-{tool}"
        d_id = await ledger.append({**base, **tr1, "timestamp": at(), "entry_type": "decision", "tool": tool,
                                    "correlation_id": cid, "reversible": reversible, "decision": "allow",
                                    "reason": "Routine move.", "summary": decision_text}, db=db)
        o_id = await ledger.append({**base, **tr1, "timestamp": at(), "entry_type": "outcome", "tool": tool,
                                    "correlation_id": cid, "reversible": reversible, "decision": "allow",
                                    "status": "sent", "refs": [d_id], "response": {"ok": True},
                                    "summary": f"The lab system accepted {tool} for TR-0001"
                                               + (f"; awaiting {scan[0]} scan." if scan else ".")}, db=db)
        if not scan:
            continue
        kind, location, scanner, to_cust, text = scan
        when = at()
        event = {"scanner_id": scanner, "type": kind, "sample_id": "TS-1042", "location_id": location,
                 "from_custodian": "location:FZ-B", "to_custodian": to_cust, "correlation_id": cid,
                 "scanned_at": when}
        event["signature"] = sign_scan(event)
        await db.custody_events.insert_one(event)  # seed runs as admin; at runtime only scanner_svc writes these
        await ledger.append({**base, **tr1, "timestamp": when, "entry_type": "verification", "tool": tool,
                             "correlation_id": cid, "reversible": False, "decision": "allow",
                             "status": "confirmed", "refs": [o_id], "summary": text}, db=db)
        if kind == "intake":
            await ledger.append({**base, **tr1, "timestamp": at(), "entry_type": "exposure",
                                 "correlation_id": cid, "decision": "allow", "reversible": None,
                                 "response": {"minutes_out": 3.0, "limit_minutes": 20, "exceeded": False},
                                 "summary": "TS-1042 was out of storage 3.0 min (limit 20): within limit."}, db=db)

    deny = {**base, "transfer_id": "TR-0002", "sample_id": "EV-2201", "request_id": "VR-0002"}
    await ledger.append({**deny, "timestamp": t0 + timedelta(minutes=39), "entry_type": "intake",
                         "actor": "user:jlee", "correlation_id": "c-hist-intake2", "decision": "allow",
                         "policy_id": "intake", "policy_version": 1, "response": {"transcript": DENIED_TEXT},
                         "summary": f"jlee requested by typing: '{DENIED_TEXT}'"}, db=db)
    await ledger.append({**deny, "timestamp": t0 + timedelta(minutes=40), "entry_type": "decision",
                         "correlation_id": "c-hist-deny", "tool": "reserve_station", "reversible": True,
                         "args": {"station_id": "SEQ-3"}, "decision": "deny",
                         "rule_hits": ["destination_not_certified"],
                         "reason": "Tech asked to move the swab to sequencing.",
                         "summary": "Agent reserve_station EV-2201 for jlee under GRANT-0007 (policy custody-default "
                                    "v3): DENIED. SEQ-3 lacks FORENSIC certification."}, db=db)
    print("seeded   history: TR-0001 (completed, with scans), TR-0002 (denied, cancelled), VR-0001, VR-0002")


def main() -> None:
    db = MongoClient(settings.mongodb_uri_admin, tz_aware=True)[settings.mongodb_db]
    if "--reset" in sys.argv:
        reset_mutable(db)
    upsert_reference(db)
    if "--reset" in sys.argv or db.ledger.estimated_document_count() == 0:
        asyncio.run(seed_history())
    print("done")


if __name__ == "__main__":
    main()

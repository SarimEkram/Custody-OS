"""Safety guard for the integration tests.

They write to the REAL shared Atlas database (test transfers, fake scans, and they reset sample records on
teardown). If a teammate is mid-run, that corrupts their transfer. So integration tests refuse to run while any
non-test transfer is unfinished, or there was ledger activity in the last few minutes.

Override on purpose with:  CUSTODY_TESTS_ALLOW_SHARED_DB=1 pytest -m integration
"""

import os
from datetime import UTC, datetime, timedelta

import pytest

TERMINAL = ["received", "recovered", "cancelled"]
QUIET_MINUTES = 5


def _live_work_reason() -> str | None:
    from pymongo import MongoClient

    from agent.config import settings

    db = MongoClient(settings.mongodb_uri_admin, tz_aware=True, serverSelectionTimeoutMS=8000)[settings.mongodb_db]
    live = [t["_id"] for t in db.transfers.find(
        {"state": {"$nin": TERMINAL}, "_id": {"$not": {"$regex": "^TR-TEST"}}}, {"_id": 1})]
    if live:
        return f"unfinished transfers on the shared database: {live}"
    cutoff = datetime.now(UTC) - timedelta(minutes=QUIET_MINUTES)
    recent = db.ledger.count_documents({"timestamp": {"$gt": cutoff}, "transfer_id": {"$not": {"$regex": "^TR-TEST"}}})
    if recent:
        return f"{recent} ledger rows written in the last {QUIET_MINUTES} minutes (someone is running the app)"
    return None


@pytest.fixture(scope="session", autouse=True)
def _protect_shared_database(request):
    wants_db = any(item.get_closest_marker("integration") for item in request.session.items)
    if not wants_db or os.environ.get("CUSTODY_TESTS_ALLOW_SHARED_DB") == "1":
        return
    reason = _live_work_reason()
    if reason:
        pytest.exit(
            "Integration tests NOT run: " + reason + ".\n"
            "They write to the shared database and would corrupt a live run. Ask the team to stop, reset, "
            "then run again (or set CUSTODY_TESTS_ALLOW_SHARED_DB=1 if you really mean it).",
            returncode=3,
        )

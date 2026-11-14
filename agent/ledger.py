"""The append-only ledger. THE ONLY MODULE THAT WRITES `ledger`.

Rules (CLAUDE.md rule 4): one function, `append()`, which calls insert_one. No update, replace or
delete anywhere (tests/test_ledger_append_only.py greps the repo). Corrections are new entries
with entry_type="correction" whose `refs` point at the entry being corrected.
The agent's DB user (agent_svc) has no update/remove privilege on `ledger` either (db/roles.md).
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from agent import embeddings
from agent.db import agent_db
from agent.models import LedgerEntry


async def append(entry: LedgerEntry | dict[str, Any], *, session: AsyncClientSession | None = None,
                 db: AsyncDatabase | None = None) -> ObjectId:
    """Validate and insert one ledger entry. Returns its _id.

    Pass `session` to include the insert in a transaction (verified custody handoff).
    """
    if isinstance(entry, dict):
        entry = LedgerEntry.model_validate(entry)
    doc = entry.to_mongo()
    doc.pop("_id", None)
    if not embeddings.AUTO and doc.get("summary"):
        doc["embedding"] = (await embeddings.embed([doc["summary"]]))[0]
    res = await (db if db is not None else agent_db()).ledger.insert_one(doc, session=session)
    return res.inserted_id

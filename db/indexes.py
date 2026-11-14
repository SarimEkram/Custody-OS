"""Create collections, attach validators, build all indexes (incl. TTL and Vector Search).

Usage:  python -m db.indexes            (idempotent; run as admin_svc)
        python -m db.indexes --no-wait  (don't wait for vector indexes to become READY)

Collections are created here because agent_svc/scanner_svc deliberately lack createCollection.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import gridfs
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.operations import SearchIndexModel

from agent.config import settings

VALIDATORS = Path(__file__).parent / "validators"

COLLECTIONS = [
    "identities", "grants", "policies", "samples", "locations", "voice_requests", "transfers", "ledger",
    "custody_events", "approvals", "reservations", "incidents", "control",
]

INDEXES: dict[str, list[tuple[list[tuple[str, int]], dict]]] = {
    "grants": [([("expires_at", ASCENDING)], {"expireAfterSeconds": 0}),  # TTL (gate also checks expires_at)
               ([("grantee", ASCENDING), ("status", ASCENDING)], {})],
    "reservations": [([("expires_at", ASCENDING)], {"expireAfterSeconds": 0})],  # TTL
    "approvals": [([("expires_at", ASCENDING)],
                   {"expireAfterSeconds": 0, "partialFilterExpression": {"status": "pending"}}),  # TTL pending only
                  ([("transfer_id", ASCENDING), ("status", ASCENDING)], {})],
    "ledger": [([("transfer_id", ASCENDING), ("timestamp", ASCENDING)], {}),
               ([("sample_id", ASCENDING), ("timestamp", ASCENDING)], {}),
               ([("correlation_id", ASCENDING)], {}),
               ([("request_id", ASCENDING)], {}),
               ([("entry_type", ASCENDING), ("decision", ASCENDING)], {})],
    "custody_events": [([("sample_id", ASCENDING), ("scanned_at", ASCENDING)], {}),
                       ([("correlation_id", ASCENDING)], {})],
    "transfers": [([("state", ASCENDING)], {}), ([("grant_id", ASCENDING)], {})],
    "voice_requests": [([("speaker_id", ASCENDING), ("created_at", DESCENDING)], {})],
}


def vector_definition(filters: list[str]) -> dict:
    if settings.embeddings_provider == "atlas_auto":
        field = {"type": "autoEmbed", "modality": "text", "path": "summary", "model": settings.auto_embed_model}
    else:
        field = {"type": "vector", "path": "embedding", "numDimensions": settings.embed_dim, "similarity": "cosine"}
    return {"fields": [field, *({"type": "filter", "path": p} for p in filters)]}


VECTOR_INDEXES = {
    "ledger": ("ledger_vector", vector_definition(["sample_id", "decision", "entry_type", "transfer_id", "tool"])),
    "incidents": ("incidents_vector", vector_definition(["tags", "severity"])),
}


def ensure_collections(db) -> None:
    existing = set(db.list_collection_names())
    for name in COLLECTIONS:
        vfile = VALIDATORS / f"{name}.json"
        opts = {}
        if vfile.exists():
            opts = {"validator": json.loads(vfile.read_text()), "validationLevel": "strict",
                    "validationAction": "error"}
        if name not in existing:
            db.create_collection(name, **opts)
            print(f"created  {name}{' (+validator)' if opts else ''}")
        elif opts:
            db.command("collMod", name, **opts)
            print(f"updated  {name} validator")


def ensure_indexes(db) -> None:
    for coll, specs in INDEXES.items():
        for keys, opts in specs:
            name = db[coll].create_index(keys, **opts)
            print(f"index    {coll}.{name}{' (TTL)' if 'expireAfterSeconds' in opts else ''}")
    # GridFS bucket indexes, so the agent never needs to create them on first upload
    bucket = gridfs.GridFSBucket(db, bucket_name="voice_audio")
    fid = bucket.upload_from_stream("_init", io.BytesIO(b""))
    bucket.delete(fid)
    print("index    voice_audio.files / voice_audio.chunks (GridFS)")


def ensure_vector_indexes(db, wait: bool) -> None:
    for coll, (name, definition) in VECTOR_INDEXES.items():
        current = next(iter(db[coll].list_search_indexes(name)), None)
        if current is None:
            db[coll].create_search_index(SearchIndexModel(name=name, type="vectorSearch", definition=definition))
            print(f"vector   {coll}.{name} created")
        elif current.get("latestDefinition") != definition:
            db[coll].update_search_index(name, definition)
            print(f"vector   {coll}.{name} updated")
        else:
            print(f"vector   {coll}.{name} unchanged ({current.get('status')})")
    if not wait:
        return
    for coll, (name, _) in VECTOR_INDEXES.items():
        start = time.time()
        while time.time() - start < 300:
            idx = next(iter(db[coll].list_search_indexes(name)), {})
            if idx.get("status") == "READY" and idx.get("queryable"):
                print(f"vector   {coll}.{name} READY")
                break
            if idx.get("status") == "FAILED":
                raise SystemExit(f"{name} FAILED: {idx}")
            time.sleep(5)
        else:
            print(f"vector   {coll}.{name} still building after 300s (continuing)")


def main() -> None:
    db = MongoClient(settings.mongodb_uri_admin)[settings.mongodb_db]
    ensure_collections(db)
    ensure_indexes(db)
    ensure_vector_indexes(db, wait="--no-wait" not in sys.argv)
    print("done")


if __name__ == "__main__":
    main()

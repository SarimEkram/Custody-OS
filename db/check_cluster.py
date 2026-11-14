"""Verify the Atlas features CustodyOS relies on. Writes db/CLUSTER_CHECK.md.

Usage:  python -m db.check_cluster
Uses `_check_*` collections and cleans them up. Safe to re-run.
"""

from __future__ import annotations

import io
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import gridfs
import httpx
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError, WriteError
from pymongo.operations import SearchIndexModel

from agent.config import settings

DB = settings.mongodb_db
VALIDATORS = Path(__file__).parent / "validators"
REPORT = Path(__file__).parent / "CLUSTER_CHECK.md"
results: list[tuple[str, str, str]] = []
_lock = threading.Lock()


def record(name: str, ok: bool | None, detail: str = "") -> None:
    status = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    with _lock:
        results.append((name, status, detail))
    print(f"{status:4}  {name:42} {detail}", flush=True)


def now() -> datetime:
    return datetime.now(timezone.utc)


def client(uri: str) -> MongoClient:
    return MongoClient(uri, serverSelectionTimeoutMS=10000)


def valid_ledger_doc() -> dict:
    return {
        "timestamp": now(), "entry_type": "decision", "actor": "agent:custody-01", "on_behalf_of": "user:jlee",
        "grant_id": "GRANT-0007", "policy_id": "custody-default", "policy_version": 3, "decision": "allow",
        "transfer_id": "TR-CHECK", "correlation_id": "c-check", "request_id": None,
    }


# ---------------------------------------------------------------- checks
def check_connect() -> dict[str, MongoClient]:
    clients = {}
    for name, uri in (("admin", settings.mongodb_uri_admin), ("agent", settings.mongodb_uri_agent),
                      ("scanner", settings.mongodb_uri_scanner)):
        try:
            c = client(uri)
            c.admin.command("ping")
            clients[name] = c
            record(f"connect {name}_svc", True)
        except PyMongoError as e:
            record(f"connect {name}_svc", False, str(e)[:80])
    if "admin" in clients:
        info = clients["admin"].server_info()
        hello = clients["admin"].admin.command("hello")
        record("server version / replica set", True, f"{info['version']} / {hello.get('setName')}")
    return clients


def check_validator(db) -> None:
    db.drop_collection("_check_ledger")
    schema = json.loads((VALIDATORS / "ledger.json").read_text())
    db.create_collection("_check_ledger", validator=schema, validationLevel="strict", validationAction="error")
    try:
        db["_check_ledger"].insert_one(valid_ledger_doc())
        record("validator accepts valid ledger entry", True)
    except WriteError as e:
        record("validator accepts valid ledger entry", False, str(e)[:80])
    bad = valid_ledger_doc()
    del bad["on_behalf_of"]
    try:
        db["_check_ledger"].insert_one(bad)
        record("validator rejects entry w/o on_behalf_of", False, "insert was accepted")
    except WriteError as e:
        record("validator rejects entry w/o on_behalf_of", e.code == 121, f"code {e.code} DocumentValidationFailure")
    db.drop_collection("_check_ledger")


def check_roles(clients: dict[str, MongoClient]) -> None:
    if "agent" not in clients or "scanner" not in clients:
        record("custom roles (layer 2)", None, "agent/scanner user missing")
        return
    agent = clients["agent"][DB]
    scanner = clients["scanner"][DB]

    def refused(fn) -> bool:
        try:
            fn()
            return False
        except OperationFailure as e:
            return e.code == 13

    record("agent cannot update ledger", refused(lambda: agent.ledger.update_one({"_id": "none"}, {"$set": {"x": 1}})),
           "expect 13 Unauthorized")
    record("agent cannot delete ledger", refused(lambda: agent.ledger.delete_one({"_id": "none"})))
    record("agent cannot write custody_events",
           refused(lambda: agent.custody_events.delete_one({"_id": "none"}))
           and refused(lambda: agent.custody_events.update_one({"_id": "none"}, {"$set": {"x": 1}})))
    record("agent cannot edit grants", refused(lambda: agent.grants.update_one({"_id": "none"}, {"$set": {"x": 1}})))
    record("scanner cannot write ledger", refused(lambda: scanner.ledger.update_one({"_id": "none"}, {"$set": {"x": 1}})))


def check_change_stream(db, other_db) -> None:
    db.drop_collection("_check_grants")
    db.create_collection("_check_grants")
    events: list[str] = []
    ready = threading.Event()

    def watch() -> None:
        with db["_check_grants"].watch(max_await_time_ms=500) as stream:
            ready.set()
            deadline = time.time() + 8
            while time.time() < deadline and len(events) < 2:
                ch = stream.try_next()
                if ch:
                    events.append(ch["operationType"])

    t = threading.Thread(target=watch)
    t.start()
    ready.wait(5)
    time.sleep(0.5)
    other_db["_check_grants"].insert_one({"_id": "G1", "status": "active"})
    other_db["_check_grants"].update_one({"_id": "G1"}, {"$set": {"status": "revoked"}})
    t.join()
    record("change stream insert + update", events == ["insert", "update"], f"got {events}")
    db.drop_collection("_check_grants")


def check_transactions(c: MongoClient) -> None:
    db = c[DB]
    for n in ("_check_tx_a", "_check_tx_b"):
        db.drop_collection(n)
        db.create_collection(n)
    with c.start_session() as s:
        with s.start_transaction():
            db["_check_tx_a"].insert_one({"_id": 1}, session=s)
            db["_check_tx_b"].insert_one({"_id": 1}, session=s)
    committed = db["_check_tx_a"].count_documents({}) == 1 and db["_check_tx_b"].count_documents({}) == 1
    record("transaction commit (2 collections)", committed)
    try:
        with c.start_session() as s:
            with s.start_transaction():
                db["_check_tx_a"].insert_one({"_id": 2}, session=s)
                db["_check_tx_b"].insert_one({"_id": 2}, session=s)
                raise RuntimeError("abort on purpose")
    except RuntimeError:
        pass
    aborted = db["_check_tx_a"].count_documents({"_id": 2}) == 0 and db["_check_tx_b"].count_documents({"_id": 2}) == 0
    record("transaction abort leaves no writes", aborted)
    for n in ("_check_tx_a", "_check_tx_b"):
        db.drop_collection(n)


def check_ttl(db) -> None:
    db.drop_collection("_check_ttl")
    db["_check_ttl"].create_index("expires_at", expireAfterSeconds=0)
    db["_check_ttl"].insert_one({"_id": "ttl", "expires_at": now()})
    start = time.time()
    while time.time() - start < 150:
        if db["_check_ttl"].count_documents({}) == 0:
            record("TTL index deletes expired doc", True, f"deleted after {time.time() - start:.0f}s")
            db.drop_collection("_check_ttl")
            return
        time.sleep(5)
    record("TTL index deletes expired doc", False, "not deleted within 150s")
    db.drop_collection("_check_ttl")


def wait_search_index(coll, name: str, timeout: int = 240) -> str:
    start = time.time()
    while time.time() - start < timeout:
        idx = next((i for i in coll.list_search_indexes(name)), None)
        if idx and idx.get("status") in ("READY", "FAILED"):
            return idx["status"] if idx.get("queryable") or idx["status"] == "FAILED" else "NOT_QUERYABLE"
        time.sleep(5)
    return "TIMEOUT"


def check_vector_search(db) -> None:
    db.drop_collection("_check_vec")
    db.create_collection("_check_vec")
    dim = settings.embed_dim
    db["_check_vec"].insert_many([{"_id": i, "tag": "t", "v": [random.random() for _ in range(dim)]} for i in range(3)])
    t0 = time.time()
    db["_check_vec"].create_search_index(SearchIndexModel(
        name="check_vec_idx", type="vectorSearch",
        definition={"fields": [{"type": "vector", "path": "v", "numDimensions": dim, "similarity": "cosine"},
                               {"type": "filter", "path": "tag"}]}))
    status = wait_search_index(db["_check_vec"], "check_vec_idx")
    if status != "READY":
        record("vector search index", False, status)
    else:
        time.sleep(3)
        rows = list(db["_check_vec"].aggregate([{"$vectorSearch": {
            "index": "check_vec_idx", "path": "v", "queryVector": [random.random() for _ in range(dim)],
            "numCandidates": 10, "limit": 3, "filter": {"tag": "t"}}}]))
        record("vector search index + $vectorSearch", len(rows) == 3,
               f"{len(rows)} results, index ready in {time.time() - t0:.0f}s")
    db.drop_collection("_check_vec")


def check_auto_embedding(db) -> None:
    """Atlas Automated Embedding: Atlas generates vectors from a text field itself."""
    db.drop_collection("_check_auto")
    db.create_collection("_check_auto")
    db["_check_auto"].insert_many([
        {"_id": 1, "summary": "Forensic DNA swab checked out of freezer without supervisor approval."},
        {"_id": 2, "summary": "Tissue sample delivered to sequencing station on time."},
        {"_id": 3, "summary": "Courier rerouted after permission was revoked mid-transfer."},
    ])
    definition = {"fields": [{"type": "autoEmbed", "modality": "text", "path": "summary",
                              "model": settings.auto_embed_model}]}
    try:
        db["_check_auto"].create_search_index(SearchIndexModel(name="check_auto_idx", type="vectorSearch",
                                                            definition=definition))
    except OperationFailure as e:
        record("automated embedding index", False, f"create refused: {str(e)[:120]}")
        db.drop_collection("_check_auto")
        return
    status = wait_search_index(db["_check_auto"], "check_auto_idx")
    if status != "READY":
        record("automated embedding index", False, status)
        db.drop_collection("_check_auto")
        return
    time.sleep(3)
    try:
        rows = list(db["_check_auto"].aggregate([{"$vectorSearch": {
            "index": "check_auto_idx", "path": "summary", "query": {"text": "evidence moved with no supervisor"},
            "numCandidates": 10, "limit": 1}}]))
        top = rows[0]["_id"] if rows else None
        record("automated embedding text query", top == 1, f"top hit _id={top} (expected 1)")
    except OperationFailure as e:
        record("automated embedding text query", False, str(e)[:120])
    db.drop_collection("_check_auto")


def check_voyage() -> None:
    if not settings.voyage_api_key:
        record("Voyage embeddings (ai.mongodb.com)", None, "no VOYAGE_API_KEY")
        return
    r = httpx.post(f"{settings.voyage_base_url}/embeddings", timeout=20,
                   headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                   json={"input": ["custody check"], "model": settings.voyage_model, "input_type": "document"})
    if r.status_code != 200:
        record("Voyage embeddings (ai.mongodb.com)", False, f"HTTP {r.status_code}")
        return
    dim = len(r.json()["data"][0]["embedding"])
    record("Voyage embeddings (ai.mongodb.com)", dim == settings.embed_dim, f"{settings.voyage_model}, {dim} dims")


def check_gridfs(clients: dict[str, MongoClient]) -> None:
    who = "agent" if "agent" in clients else "admin"
    db = clients[who][DB]
    bucket = gridfs.GridFSBucket(db, bucket_name="voice_audio")
    data = b"fake-webm-bytes" * 100
    fid = bucket.upload_from_stream("_check.webm", io.BytesIO(data), metadata={"check": True})
    out = io.BytesIO()
    bucket.download_to_stream(fid, out)
    record(f"GridFS voice_audio upload/download ({who}_svc)", out.getvalue() == data, f"{len(data)} bytes")
    admin_bucket = gridfs.GridFSBucket(clients["admin"][DB], bucket_name="voice_audio")
    admin_bucket.delete(fid)  # agent can't delete; admin cleans up


# ---------------------------------------------------------------- main
def main() -> None:
    started = now()
    clients = check_connect()
    if "admin" not in clients:
        raise SystemExit("admin connection failed; fix MONGODB_URI_ADMIN first")
    admin_db = clients["admin"][DB]
    second = client(settings.mongodb_uri_admin)[DB]

    with ThreadPoolExecutor(max_workers=3) as pool:
        slow = [pool.submit(check_ttl, admin_db), pool.submit(check_vector_search, admin_db),
                pool.submit(check_auto_embedding, admin_db)]
        check_validator(admin_db)
        check_roles(clients)
        check_change_stream(admin_db, second)
        check_transactions(clients["admin"])
        check_voyage()
        check_gridfs(clients)
        print("... waiting for TTL monitor and search index builds (up to ~4 min)", flush=True)
        for f in slow:
            f.result()

    passed = sum(1 for _, s, _ in results if s == "PASS")
    lines = [
        "# Cluster check", "",
        f"Run {started:%Y-%m-%d %H:%M} UTC by `python -m db.check_cluster`. {passed}/{len(results)} passed.", "",
        "| Check | Result | Detail |", "|---|---|---|",
        *[f"| {n} | {s} | {d.replace('|', '/')} |" for n, s, d in results],
    ]
    REPORT.write_text("\n".join(lines) + "\n")
    print(f"\n{passed}/{len(results)} passed. Wrote {REPORT.relative_to(REPORT.parent.parent)}")


if __name__ == "__main__":
    main()

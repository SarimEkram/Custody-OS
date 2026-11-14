"""Smoke-test agent/api.py routes through FastAPI against Atlas.  Run: pytest -m integration"""

import httpx
import pytest
from fastapi import FastAPI

from agent.api import router

pytestmark = pytest.mark.integration
app = FastAPI()
app.include_router(router)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_audit_routes(client):
    assert (await client.get("/audit/gaps")).status_code == 200
    r = (await client.get("/audit/restricted-without-supervisor")).json()
    assert r["violations"] == []
    chain = (await client.get("/audit/chain/ts-1042")).json()
    assert chain and all("embedding" not in row for row in chain)
    q = (await client.post("/audit/query", json={"question": "What happened to TS-1042?"})).json()
    assert q["route"] == "custody_chain"


async def test_human_action_errors_are_clean(client):
    assert (await client.post("/approvals/APR-NOPE", json={"decided_by": "user:mokafor", "approve": True})
            ).status_code == 400
    assert (await client.post("/grants/GRANT-NOPE/revoke", json={"by": "user:jlee"})).status_code == 404
    assert (await client.post("/transfers/TR-NOPE/confirm",
                              json={"user_id": "user:jlee", "readback_text": "x"})).status_code == 400


# ---- C's dashboard client shapes (dashboard/src/lib/api.ts) must work alongside the original ones
async def test_dashboard_shaped_bodies_pass_validation(client):
    # Unknown ids give 400/404/403 (domain errors), never 422 (body didn't validate)
    cases = [
        ("/transfers/TR-NOPE/confirm", {"user_id": "user:jlee", "method": "button"}),
        ("/approvals/APR-NOPE", {"user_id": "user:mokafor", "decision": "approved"}),
        ("/approvals/APR-NOPE", {"user_id": "user:mokafor", "decision": "denied"}),
        ("/approvals/APR-NOPE", {"decided_by": "user:mokafor", "approve": True}),
        ("/grants/GRANT-NOPE/revoke", {"user_id": "user:jlee"}),
        ("/grants/GRANT-NOPE/revoke", {"by": "user:jlee", "reason": "x"}),
    ]
    for path, body in cases:
        assert (await client.post(path, json=body)).status_code in (400, 403, 404), (path, body)
    assert (await client.post("/approvals/APR-NOPE", json={"user_id": "user:mokafor"})).status_code == 422


async def test_stop_accepts_dashboard_body(client):
    from agent.db import admin_db
    r = await client.post("/control/stop", json={"user_id": "user:testapi", "scope": "transfer",
                                                 "transfer_id": "TR-NOPE-STOP"})
    assert r.status_code == 200
    await admin_db().control.delete_many({"issued_by": "user:testapi"})  # admin cleanup of test row


async def test_audit_query_has_dashboard_fields(client):
    r = (await client.post("/audit/query", json={"question": "What happened to TS-1042?", "user_id": "user:jlee"})).json()
    assert r["route"] == "custody_chain" and r["rows"]  # original fields kept
    assert r["mode"] == "canned" and r["answer"] and r["entries"]
    assert all(e["entry_type"] != "scan" for e in r["entries"])
    r = (await client.post("/audit/query", json={"question": "Show every restricted move without a supervisor"})).json()
    assert r["route"] == "restricted_without_supervisor" and "0 restricted move" in r["answer"]
    assert any(e["decision"] in ("deny", "pending_approval") for e in r["entries"])  # beat-2 blocked attempt


async def test_health_and_state(client):
    h = (await client.get("/health")).json()
    assert h == {"status": "ok", "mongo": "up"}
    st = (await client.get("/state")).json()
    for key in ("transfers", "ledger", "scans", "approvals", "samples", "locations", "grants",
                "demo_scripted", "demo_speed"):
        assert key in st, key
    assert {s["_id"] for s in st["samples"]} >= {"TS-1042", "EV-2201", "TS-1077"}
    assert len(st["locations"]) == 5 and st["grants"][0]["_id"]
    assert all("embedding" not in row for row in st["ledger"])
    stamps = [row["timestamp"] for row in st["ledger"]]
    assert stamps == sorted(stamps)  # oldest first
    assert isinstance(st["demo_scripted"], bool) and st["demo_speed"] > 0

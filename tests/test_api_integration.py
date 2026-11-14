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

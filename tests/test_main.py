"""Wiring: routes, precedence, watcher callbacks. Every backend is a fake (no Atlas, no network)."""

import asyncio
import json

import httpx
from fastapi import APIRouter
from fastapi.testclient import TestClient

from agent.events import EventHub
from agent.intake import MemoryRequests
from agent.main import Backends, build_service
from agent.parse import Catalog
from agent.scripted import ScriptedLLM, rule_parser
from agent.transfers import MemoryCustodyStore
from agent.voice import MemoryAudio

CATALOG = Catalog({"TS-1042": "FZ-B"}, {"FZ-B": "Freezer B", "SEQ-3": "Sequencing Station 3"})


async def noop(*a, **k):
    return None


def backends(**over):
    atlas_router = APIRouter()

    @atlas_router.get("/audit/gaps")
    async def gaps():
        return []

    @atlas_router.get("/stream")  # A's version: B's merged stream must win
    async def a_stream():
        return {"from": "A"}

    async def find_grant(user_id):
        return "GRANT-0007" if user_id == "user:jlee" else None

    async def feed():
        await asyncio.sleep(0.05)
        yield {"kind": "ledger", "data": {"n": 1}}
        await asyncio.sleep(5)

    lab = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    calls = {"applied": [], "recorded": []}

    async def apply_scan(scan):
        calls["applied"].append(scan)
        return "checked_out"

    async def recorder(tid, user, text, method):
        calls["recorded"].append((tid, user, text, method))

    base = dict(
        store=MemoryCustodyStore({}, {}), requests=MemoryRequests(), audio=MemoryAudio(),
        catalog=CATALOG, find_grant=find_grant, append=noop, execute=noop, lab=lab,
        llm=ScriptedLLM([]), parser=rule_parser(CATALOG), stt=None, router=atlas_router,
        event_feed=feed, apply_scan=apply_scan, confirm_recorder=recorder,
        record_unverified=noop, read_outcomes=noop, job_state=noop, list_jobs=noop,
        freezer_certs={}, next_state={},
    )  # fmt: skip
    return Backends(**{**base, **over}), calls


def paths(app):
    return set(app.openapi()["paths"])


def test_the_service_exposes_b_routes_and_mounts_as_routes_without_clashing():
    service = build_service(backends()[0])
    have = paths(service.app)
    assert {"/transfers", "/transfers/{transfer_id}/confirm", "/stream", "/audit/gaps"} <= have


def test_voice_route_exists_only_when_speech_to_text_is_configured():
    assert "/voice/requests" not in paths(build_service(backends()[0]).app)

    class STT:
        model_id = "scribe_v3"

    with_stt = build_service(backends(stt=STT())[0])
    assert "/voice/requests" in paths(with_stt.app)


def test_audio_routes_exist_only_when_text_to_speech_is_configured():
    without = paths(build_service(backends()[0]).app)
    assert "/transfers/{transfer_id}/readback/audio" not in without
    configured = backends(tts=object(), get_approval=noop)[0]
    with_tts = paths(build_service(configured).app)
    assert "/transfers/{transfer_id}/readback/audio" in with_tts
    assert "/approvals/{approval_id}/audio" in with_tts


def test_the_published_readback_points_at_the_audio_route_only_with_text_to_speech():
    assert build_service(backends()[0]).confirmations.audio_url is None
    configured = build_service(backends(tts=object(), get_approval=noop)[0])
    assert configured.confirmations.audio_url("TR-1") == "/api/transfers/TR-1/readback/audio"


def test_b_owns_the_stream_route_so_confirmation_and_courier_events_reach_the_dashboard():
    service = build_service(backends()[0])
    with TestClient(service.app).stream("GET", "/stream") as r:
        # A's version in this test returns JSON; B's is the SSE merge of Atlas + B events
        assert r.headers["content-type"].startswith("text/event-stream")
        assert next(r.iter_lines()) == ": connected"


def test_merged_stream_delivers_atlas_events_and_b_events_on_one_connection():
    service = build_service(backends()[0])

    async def go():
        stream = service.stream_events()
        got = []

        async def collect():
            async for ev in stream:
                got.append(ev)
                if len(got) == 2:
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.02)
        service.hub.publish({"kind": "courier_position", "data": {"progress": 0.5}})
        await asyncio.wait_for(task, 2)
        return got

    assert {e["kind"] for e in asyncio.run(go())} == {"ledger", "courier_position"}


def test_the_confirm_route_accepts_as_bodys_shape_and_records_through_the_gate():
    b, calls = backends()
    service = build_service(b)
    transfer = {"_id": "TR-0001", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3",
                "requested_by": "user:jlee", "state": "reserved", "history": []}  # fmt: skip
    asyncio.run(b.store.create_transfer(transfer))
    out = asyncio.run(service.confirmations.request("TR-0001", 4))
    client = TestClient(service.app)
    body = {"user_id": "user:jlee", "readback_text": "whatever the browser sent",
            "method": "button"}  # fmt: skip
    r = client.post("/transfers/TR-0001/confirm", json=body)
    assert r.status_code == 200
    assert calls["recorded"] == [("TR-0001", "user:jlee", out["readback_text"], "button")]


def test_watcher_callbacks_are_wired_to_the_signals_the_loop_waits_on_and_to_halt():
    b, calls = backends()
    service = build_service(b)
    halted = []

    async def fake_halt(tid, by):
        halted.append((tid, by))

    service.runner.halt = fake_halt

    async def go():
        await service.on_approval({"_id": "APR-1", "status": "approved"})
        await service.on_halt({"_id": "TR-0001"}, "grant_revoked", "user:mokafor")
        await service.on_scan({"correlation_id": "c-1", "type": "checkout"})
        return await service.signals.wait("approval:APR-1", 0.05)

    assert asyncio.run(go()) == "approved"
    assert halted == [("TR-0001", "user:mokafor")]
    assert calls["applied"] == [{"correlation_id": "c-1", "type": "checkout"}]


def test_notify_is_recorded_and_returns_who_was_told():
    service = build_service(backends()[0])
    out = asyncio.run(service.internal["notify"]({"user_id": "user:jlee", "message": "hi"}, None))
    assert out == {"sent_to": "user:jlee"} and service.notifications == [("user:jlee", "hi")]
    assert json.dumps(out)


def test_a_typed_request_flows_to_a_transfer_with_the_scripted_parser():
    b, _ = backends()
    service = build_service(b)
    client = TestClient(service.app)
    r = client.post("/transfers", json={"user_id": "user:jlee",
        "text": "Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM."})  # fmt: skip
    assert r.status_code == 201 and r.json()["transfer_id"] == "TR-0001"
    assert isinstance(service.hub, EventHub)

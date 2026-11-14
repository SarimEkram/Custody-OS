"""The three open issues in DASHBOARD_BACKEND_ISSUES.md. No network, no database.

1. grant reissue (supervisor only, new grant, audited)
2. original-audio route
3. courier reroute fields
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent import api, control
from agent.events import CourierTicker, EventHub
from agent.intake_api import create_app
from agent.models import LedgerEntry
from agent.voice import sniff_audio_type
from services.courier.app import create_app as courier_app

NOW = datetime(2026, 11, 14, 19, 0, tzinfo=UTC)
OLD = {
    "_id": "GRANT-0007",
    "grantor": "user:jlee",
    "grantee": "agent:custody-01",
    "status": "revoked",
    "scope": {
        "actions": ["lookup_sample", "checkout_sample"],
        "sample_types": ["tissue"],
        "locations": ["FZ-A", "SEQ-3"],
        "max_restriction": "forensic",
    },
    "created_at": NOW - timedelta(hours=2),
    "expires_at": NOW + timedelta(hours=4),
    "revoked_at": NOW - timedelta(minutes=5),
    "revoked_by": "user:jlee",
}
SUPERVISOR = {"_id": "user:mokafor", "kind": "human", "role": "supervisor"}
TECH = {"_id": "user:jlee", "kind": "human", "role": "lab_tech"}
POLICY = {"policy_id": "custody-default", "version": 3}
GOOD = "Revoked during a rehearsal. Reviewed with the technician."


def plan(old=OLD, by="user:mokafor", who=SUPERVISOR, reason=GOOD, active_exists=False):
    return control.plan_reissue(old, by, who, reason, NOW, "GRANT-0008", active_exists, POLICY)


# ------------------------------------------------------------ issue 1: reissue, the rules
def test_a_supervisor_gets_a_new_grant_with_the_same_scope_and_parties():
    new, _ = plan()
    assert new["_id"] == "GRANT-0008" and new["status"] == "active"
    assert (new["grantor"], new["grantee"]) == ("user:jlee", "agent:custody-01")
    assert new["scope"] == OLD["scope"] and new["supersedes"] == "GRANT-0007"
    assert new["issued_by"] == "user:mokafor"


def test_the_scope_is_copied_not_shared_and_the_expiry_is_set_by_the_server():
    new, _ = plan()
    new["scope"]["actions"].append("deliver")
    assert "deliver" not in OLD["scope"]["actions"]
    assert new["expires_at"] == NOW + timedelta(minutes=control.REISSUE_MINUTES)


def test_a_technician_cannot_issue_a_grant():
    with pytest.raises(PermissionError, match="Only a supervisor"):
        plan(by="user:jlee", who=TECH)


def test_the_agent_cannot_grant_itself_authority():
    with pytest.raises(PermissionError, match="agent cannot grant itself"):
        plan(by="agent:custody-01", who={"_id": "agent:custody-01", "kind": "agent"})


def test_an_unknown_identity_is_not_a_supervisor():
    with pytest.raises(PermissionError, match="Only a supervisor"):
        plan(by="user:ghost", who=None)


@pytest.mark.parametrize("reason", ["", "short", "       x       "])
def test_a_reason_of_at_least_eight_characters_is_required(reason):
    with pytest.raises(ValueError, match="at least 8"):
        plan(reason=reason)


def test_an_unknown_grant_is_not_found():
    with pytest.raises(LookupError, match="unknown grant"):
        plan(old=None)


def test_an_active_grant_has_nothing_to_replace():
    with pytest.raises(control.Conflict, match="still active"):
        plan(old={**OLD, "status": "active"})


def test_a_grant_can_be_replaced_only_once():
    with pytest.raises(control.Conflict, match="already replaced by GRANT-0008"):
        plan(old={**OLD, "superseded_by": "GRANT-0008"})


def test_a_second_active_grant_for_the_same_agent_is_refused():
    with pytest.raises(control.Conflict, match="active grant already exists"):
        plan(active_exists=True)


def test_the_order_of_refusals_never_leaks_grant_details_to_a_non_supervisor():
    with pytest.raises(PermissionError):  # not 404, not 409
        plan(old=None, by="user:jlee", who=TECH)
    with pytest.raises(PermissionError):
        plan(old={**OLD, "status": "active"}, by="user:jlee", who=TECH)


# ------------------------------------------------------------ issue 1: the ledger row
def test_the_ledger_row_is_a_correction_the_dashboard_recognises_and_the_validator_accepts():
    new, row = plan()
    required = json.load(open("db/validators/ledger.json"))["$jsonSchema"]["required"]
    assert all(k in row for k in required)
    LedgerEntry.model_validate(row)
    assert row["entry_type"] == "correction" and row["rule_hits"] == ["grant_reissued"]
    assert row["actor"] == "user:mokafor" and row["on_behalf_of"] == "user:jlee"
    assert row["grant_id"] == "GRANT-0008" and row["decision"] == "allow"
    assert row["reason"] == GOOD and row["transfer_id"] == "SYSTEM"
    assert row["response"]["supersedes"] == "GRANT-0007"
    assert "GRANT-0008" in row["summary"] and "GRANT-0007" in row["summary"]


# ------------------------------------------------------------ issue 1: the route
@pytest.fixture
def reissue_client(monkeypatch):
    calls = []

    async def fake(grant_id, by, reason=""):
        calls.append((grant_id, by, reason))
        outcome = fake.outcome
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    fake.outcome = {
        "grant_id": "GRANT-0008",
        "supersedes": "GRANT-0007",
        "expires_at": NOW + timedelta(minutes=60),
    }
    monkeypatch.setattr(api, "reissue_grant", fake)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), fake, calls


def test_the_route_returns_the_dashboards_success_shape(reissue_client):
    client, _, calls = reissue_client
    r = client.post("/grants/GRANT-0007/reissue", json={"by": "user:mokafor", "reason": GOOD})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["grant_id"] == "GRANT-0008"
    assert body["supersedes"] == "GRANT-0007" and body["expires_at"].startswith(
        "2026-11-14T20:00:00"
    )
    assert calls == [("GRANT-0007", "user:mokafor", GOOD)]


def test_scope_and_expiry_in_the_body_are_ignored(reissue_client):
    client, _, calls = reissue_client
    body = {
        "by": "user:mokafor",
        "reason": GOOD,
        "scope": {"actions": ["everything"]},
        "expires_at": "2099-01-01T00:00:00Z",
    }
    assert client.post("/grants/GRANT-0007/reissue", json=body).status_code == 200
    assert calls == [("GRANT-0007", "user:mokafor", GOOD)]  # nothing else reaches the service


@pytest.mark.parametrize(
    "error, status",
    [
        (PermissionError("Only a supervisor can issue a new grant."), 403),
        (LookupError("unknown grant GRANT-9"), 404),
        (control.Conflict("GRANT-0007 is still active, so there is nothing to replace."), 409),
        (ValueError("A reason of at least 8 characters is required."), 422),
    ],
)
def test_errors_map_to_the_status_codes_with_readable_detail(reissue_client, error, status):
    client, fake, _ = reissue_client
    fake.outcome = error
    r = client.post("/grants/GRANT-0007/reissue", json={"by": "user:mokafor", "reason": GOOD})
    assert r.status_code == status and r.json()["detail"] == str(error)


def test_a_body_without_by_is_a_422(reissue_client):
    client, _, calls = reissue_client
    assert client.post("/grants/GRANT-0007/reissue", json={"reason": GOOD}).status_code == 422
    assert calls == []


# ------------------------------------------------------------ issue 2: original audio
WEBM = b"\x1aE\xdf\xa3" + bytes(range(60))


class Requests:
    def __init__(self, docs):
        self.docs = docs

    async def get(self, rid):
        return self.docs[rid]


class Audio:
    def __init__(self, files):
        self.files = files

    async def get(self, file_id):
        return self.files[file_id]

    async def put(self, *a, **k):
        raise AssertionError("the audio is evidence; the route must never write it")


def audio_client():
    docs = {
        "VR-1": {"_id": "VR-1", "source": "voice", "audio_file_id": "F1"},
        "VR-2": {"_id": "VR-2", "source": "typed", "audio_file_id": None},
    }
    return TestClient(create_app(requests=Requests(docs), audio=Audio({"F1": WEBM})))


def test_a_spoken_requests_audio_streams_with_its_type_and_length():
    r = audio_client().get("/voice/requests/VR-1/audio")
    assert r.status_code == 200 and r.content == WEBM
    assert r.headers["content-type"].startswith("audio/webm")
    assert r.headers["content-length"] == str(len(WEBM)) and r.headers["accept-ranges"] == "bytes"


def test_a_range_request_gets_the_slice_so_seeking_works():
    r = audio_client().get("/voice/requests/VR-1/audio", headers={"Range": "bytes=4-9"})
    assert r.status_code == 206 and r.content == WEBM[4:10]
    assert r.headers["content-range"] == f"bytes 4-9/{len(WEBM)}"
    tail = audio_client().get("/voice/requests/VR-1/audio", headers={"Range": "bytes=-5"})
    assert tail.status_code == 206 and tail.content == WEBM[-5:]


def test_a_range_past_the_end_is_unsatisfiable():
    r = audio_client().get("/voice/requests/VR-1/audio", headers={"Range": "bytes=9999-"})
    assert r.status_code == 416


def test_a_typed_or_unknown_request_is_a_404_with_a_readable_detail():
    typed = audio_client().get("/voice/requests/VR-2/audio")
    assert typed.status_code == 404 and "no audio" in typed.json()["detail"].lower()
    unknown = audio_client().get("/voice/requests/VR-9/audio")
    assert unknown.status_code == 404 and "VR-9" in unknown.json()["detail"]


@pytest.mark.parametrize(
    "head, kind",
    [
        (b"\x1aE\xdf\xa3....", "audio/webm"),
        (b"OggS....", "audio/ogg"),
        (b"RIFF....WAVE", "audio/wav"),
        (b"....ftypM4A ", "audio/mp4"),
        (b"ID3\x04....", "audio/mpeg"),
        (b"??????", "application/octet-stream"),
    ],
)
def test_the_audio_type_is_recognised_from_the_recording_itself(head, kind):
    assert sniff_audio_type(head) == kind


def test_the_route_is_absent_when_no_stores_are_given():
    assert TestClient(create_app()).get("/voice/requests/VR-1/audio").status_code == 404


# ------------------------------------------------------------ issue 3: courier reroute fields
TRAVEL = {("FZ-A", "SEQ-3"): 6, ("FZ-B", "SEQ-3"): 4}
READERS = {"FZ-A": "scanner:reader-fzA", "FZ-B": "scanner:reader-fzB"}


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def courier():
    clock = Clock()
    return TestClient(
        courier_app(TRAVEL, READERS, lambda s: None, clock=clock, demo_speed=60)
    ), clock


def started(client):
    jid = client.post(
        "/jobs",
        json={"sample_id": "TS-1", "origin": "FZ-B", "destination": "SEQ-3", "transfer_id": "TR-1"},
    ).json()["job_id"]
    client.post(f"/jobs/{jid}/pickup", headers={"X-Correlation-Id": "c-1"})
    return jid


def test_a_job_that_was_never_rerouted_has_no_new_fields(courier):
    client, clock = courier
    jid = started(client)
    clock.t += 1
    body = client.get(f"/jobs/{jid}").json()
    assert "original_to" not in body and "return_from_progress" not in body


def test_a_reroute_back_to_origin_remembers_the_original_route_and_how_far_it_got(courier):
    client, clock = courier
    jid = started(client)
    clock.t += 2  # halfway to SEQ-3
    body = client.post(f"/jobs/{jid}/reroute", json={"destination": "FZ-B"}).json()
    assert body["original_to"] == "SEQ-3" and body["return_from_progress"] == pytest.approx(0.5)
    clock.t += 1
    later = client.get(f"/jobs/{jid}").json()
    assert later["original_to"] == "SEQ-3" and later["return_from_progress"] == pytest.approx(0.5)
    assert later["origin"] == "FZ-B" and later["destination"] == "FZ-B"


def test_the_ticker_adds_the_fields_only_for_rerouted_jobs():
    plain = {
        "job_id": "CJ-1",
        "transfer_id": "TR-1",
        "sample_id": "TS-1",
        "origin": "FZ-B",
        "destination": "SEQ-3",
        "status": "in_transit",
        "progress": 0.5,
    }
    rerouted = {
        **plain,
        "job_id": "CJ-2",
        "destination": "FZ-B",
        "progress": 0.35,
        "original_to": "SEQ-3",
        "return_from_progress": 0.42,
    }

    async def go():
        hub = EventHub()
        got = []

        async def listen():
            async for ev in hub.subscribe():
                got.append(ev)
                if len(got) == 2:
                    return

        task = asyncio.create_task(listen())
        await asyncio.sleep(0.02)

        async def jobs():
            return [plain, rerouted]

        await CourierTicker(jobs, hub).tick()
        await asyncio.wait_for(task, 1)
        return got

    a, b = (e["data"] for e in asyncio.run(go()))
    assert set(a) == {"job_id", "transfer_id", "sample_id", "from", "to", "progress"}
    assert b["original_to"] == "SEQ-3" and b["return_from_progress"] == 0.42
    assert (b["from"], b["to"], b["progress"]) == ("FZ-B", "FZ-B", 0.35)

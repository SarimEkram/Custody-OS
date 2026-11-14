import pytest
from fastapi.testclient import TestClient

from services.courier.app import create_app

TRAVEL = {("FZ-A", "SEQ-3"): 6, ("FZ-B", "SEQ-3"): 4, ("FZ-B", "FOR-LAB"): 8}
READERS = {"FZ-A": "scanner:reader-fzA", "FZ-B": "scanner:reader-fzB"}
CID = {"X-Correlation-Id": "c-1"}


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def ctx():
    clock, scans = Clock(), []
    app = create_app(TRAVEL, READERS, scans.append, clock=clock, demo_speed=60)
    return TestClient(app), clock, scans


def job(client, origin="FZ-B", dest="SEQ-3"):
    return client.post(
        "/jobs",
        json={"sample_id": "TS-1042", "origin": origin, "destination": dest, "transfer_id": "TR-1"},
    )


def test_job_gets_eta_from_the_travel_table_in_either_direction(ctx):
    client, _, _ = ctx
    body = job(client).json()
    assert body["job_id"] == "CJ-1" and body["eta_minutes"] == 4 and body["status"] == "assigned"
    assert job(client, origin="SEQ-3", dest="FZ-B").json()["eta_minutes"] == 4


def test_unknown_route_is_rejected(ctx):
    client, _, _ = ctx
    assert job(client, origin="FZ-A", dest="FOR-LAB").status_code == 400


def test_pickup_emits_handoff_scan_from_the_origin_reader(ctx):
    client, _, scans = ctx
    jid = job(client).json()["job_id"]
    r = client.post(f"/jobs/{jid}/pickup", headers=CID)
    assert r.status_code == 200 and r.json()["scan_emitted"] is True
    [s] = scans
    assert s["type"] == "handoff" and s["reader_id"] == "scanner:reader-fzB"
    assert s["location_id"] == "FZ-B" and s["correlation_id"] == "c-1"
    assert s["from_custodian"] == "location:FZ-B" and s["to_custodian"] == f"courier:{jid}"


def test_pickup_guards(ctx):
    client, _, scans = ctx
    jid = job(client).json()["job_id"]
    assert client.post(f"/jobs/{jid}/pickup").status_code == 400  # no correlation id
    assert client.post("/jobs/CJ-99/pickup", headers=CID).status_code == 404
    client.post(f"/jobs/{jid}/pickup", headers=CID)
    assert client.post(f"/jobs/{jid}/pickup", headers=CID).status_code == 409
    assert len(scans) == 1


def test_progress_follows_the_clock_scaled_by_demo_speed(ctx):
    client, clock, _ = ctx
    jid = job(client).json()["job_id"]
    assert client.get(f"/jobs/{jid}").json()["progress"] == 0
    client.post(f"/jobs/{jid}/pickup", headers=CID)
    clock.t += 1  # 1 real second = 1 simulated minute, eta 4
    state = client.get(f"/jobs/{jid}").json()
    assert state["progress"] == pytest.approx(0.25) and state["status"] == "in_transit"
    clock.t += 10
    state = client.get(f"/jobs/{jid}").json()
    assert state["progress"] == 1 and state["status"] == "arrived"


def test_cancel_only_before_pickup(ctx):
    client, _, _ = ctx
    a = job(client).json()["job_id"]
    assert client.delete(f"/jobs/{a}").json()["status"] == "cancelled"
    b = job(client).json()["job_id"]
    client.post(f"/jobs/{b}/pickup", headers=CID)
    assert client.delete(f"/jobs/{b}").status_code == 409


def test_reroute_back_to_origin_takes_as_long_as_the_trip_so_far(ctx):
    client, clock, _ = ctx
    jid = job(client).json()["job_id"]
    client.post(f"/jobs/{jid}/pickup", headers=CID)
    clock.t += 2  # halfway
    r = client.post(f"/jobs/{jid}/reroute", json={"destination": "FZ-B"})
    assert r.status_code == 200 and r.json()["eta_minutes"] == pytest.approx(2)
    clock.t += 3
    state = client.get(f"/jobs/{jid}").json()
    assert state["status"] == "arrived" and state["destination"] == "FZ-B"


def test_reroute_guards(ctx):
    client, _, _ = ctx
    jid = job(client).json()["job_id"]
    assert client.post(f"/jobs/{jid}/reroute", json={"destination": "FZ-B"}).status_code == 409
    client.post(f"/jobs/{jid}/pickup", headers=CID)
    assert client.post(f"/jobs/{jid}/reroute", json={"destination": "NOWHERE"}).status_code == 400


def test_flaky_courier_picks_up_but_emits_no_scan(ctx):
    client, _, scans = ctx
    jid = job(client).json()["job_id"]
    client.post("/admin/flaky", json={"drop_scans": True})
    r = client.post(f"/jobs/{jid}/pickup", headers=CID)
    assert r.status_code == 200 and r.json()["scan_emitted"] is False and scans == []

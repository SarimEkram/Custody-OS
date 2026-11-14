import pytest
from fastapi.testclient import TestClient

from services.station.app import create_app

CID = {"X-Correlation-Id": "c-9"}


@pytest.fixture
def ctx():
    scans = []
    app = create_app(scans.append, reader_id="scanner:reader-seq3", location_id="SEQ-3")
    return TestClient(app), scans


def test_intake_records_the_sample_and_emits_an_intake_scan(ctx):
    client, scans = ctx
    r = client.post(
        "/intake", json={"sample_id": "TS-1042", "from_custodian": "courier:CJ-1"}, headers=CID
    )
    assert r.status_code == 200 and r.json()["scan_emitted"] is True
    assert client.get("/status").json()["received"] == ["TS-1042"]
    [s] = scans
    assert (s["type"], s["location_id"], s["reader_id"]) == (
        "intake",
        "SEQ-3",
        "scanner:reader-seq3",
    )
    assert s["correlation_id"] == "c-9"
    assert s["from_custodian"] == "courier:CJ-1" and s["to_custodian"] == "location:SEQ-3"


def test_intake_requires_a_correlation_id(ctx):
    client, scans = ctx
    assert client.post("/intake", json={"sample_id": "TS-1042"}).status_code == 400
    assert scans == []


def test_second_intake_of_the_same_sample_is_a_conflict(ctx):
    client, scans = ctx
    client.post("/intake", json={"sample_id": "TS-1042"}, headers=CID)
    assert client.post("/intake", json={"sample_id": "TS-1042"}, headers=CID).status_code == 409
    assert len(scans) == 1


def test_flaky_station_receives_but_emits_no_scan(ctx):
    client, scans = ctx
    client.post("/admin/flaky", json={"drop_scans": True})
    r = client.post("/intake", json={"sample_id": "TS-1042"}, headers=CID)
    assert r.json()["scan_emitted"] is False and scans == []

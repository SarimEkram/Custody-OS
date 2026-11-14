import pytest
from fastapi.testclient import TestClient

from services.station.app import create_app

CID = {"X-Correlation-Id": "c-9"}
READERS = {"SEQ-3": "scanner:reader-seq3", "FOR-LAB": "scanner:reader-for"}


@pytest.fixture
def ctx():
    scans = []
    return TestClient(create_app(scans.append, readers=READERS)), scans


def intake(client, station="SEQ-3", **extra):
    return client.post(
        "/intake", json={"sample_id": "TS-1042", "station_id": station, **extra}, headers=CID
    )


def test_intake_records_the_sample_and_emits_an_intake_scan(ctx):
    client, scans = ctx
    r = intake(client, from_custodian="courier:CJ-1")
    assert r.status_code == 200 and r.json()["scan_emitted"] is True
    assert client.get("/status").json()["received"]["SEQ-3"] == ["TS-1042"]
    [s] = scans
    assert (s["type"], s["location_id"], s["reader_id"]) == (
        "intake",
        "SEQ-3",
        "scanner:reader-seq3",
    )
    assert s["correlation_id"] == "c-9"
    assert s["from_custodian"] == "courier:CJ-1" and s["to_custodian"] == "location:SEQ-3"


def test_each_station_scans_with_its_own_reader(ctx):
    client, scans = ctx
    intake(client, station="FOR-LAB")
    assert scans[0]["location_id"] == "FOR-LAB" and scans[0]["reader_id"] == "scanner:reader-for"


def test_unknown_station_is_rejected_with_no_scan(ctx):
    client, scans = ctx
    assert intake(client, station="NOPE").status_code == 400
    assert scans == []


def test_intake_requires_a_correlation_id(ctx):
    client, scans = ctx
    r = client.post("/intake", json={"sample_id": "TS-1042", "station_id": "SEQ-3"})
    assert r.status_code == 400
    assert scans == []


def test_second_intake_of_the_same_sample_is_a_conflict(ctx):
    client, scans = ctx
    intake(client)
    assert intake(client).status_code == 409
    assert len(scans) == 1


def test_flaky_station_receives_but_emits_no_scan(ctx):
    client, scans = ctx
    client.post("/admin/flaky", json={"drop_scans": True})
    r = intake(client)
    assert r.json()["scan_emitted"] is False and scans == []

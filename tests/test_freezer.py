import pytest
from fastapi.testclient import TestClient

from services.freezer.app import create_app

READERS = {"FZ-B": "scanner:reader-fzB", "FZ-A": "scanner:reader-fzA"}


@pytest.fixture
def ctx():
    scans = []
    inventory = {"TS-1042": "FZ-B", "TS-1077": "FZ-A"}
    client = TestClient(create_app(inventory, scans.append, readers=READERS))
    return client, scans, inventory


def test_checkout_marks_sample_out_and_emits_scan_with_correlation_id(ctx):
    client, scans, inventory = ctx
    r = client.post(
        "/checkout", json={"sample_id": "TS-1042"}, headers={"X-Correlation-Id": "c-7f3a"}
    )
    assert r.status_code == 200
    assert r.json()["correlation_id"] == "c-7f3a"
    assert "TS-1042" not in inventory
    [s] = scans
    assert (s["type"], s["sample_id"], s["correlation_id"]) == ("checkout", "TS-1042", "c-7f3a")
    assert s["reader_id"] == "scanner:reader-fzB" and s["location_id"] == "FZ-B"


def test_checkout_scans_at_the_freezer_that_holds_the_sample(ctx):
    client, scans, _ = ctx
    client.post("/checkout", json={"sample_id": "TS-1077"}, headers={"X-Correlation-Id": "c-2"})
    assert scans[0]["location_id"] == "FZ-A" and scans[0]["reader_id"] == "scanner:reader-fzA"


def test_checkout_unknown_sample_is_404_and_emits_no_scan(ctx):
    client, scans, _ = ctx
    r = client.post("/checkout", json={"sample_id": "TS-9999"}, headers={"X-Correlation-Id": "c-1"})
    assert r.status_code == 404
    assert scans == []


def test_checkout_requires_a_correlation_id(ctx):
    client, scans, _ = ctx
    r = client.post("/checkout", json={"sample_id": "TS-1042"})
    assert r.status_code == 400
    assert scans == []


def test_checkin_returns_sample_to_the_chosen_freezer_and_emits_checkin_scan(ctx):
    client, scans, inventory = ctx
    client.post("/checkout", json={"sample_id": "TS-1042"}, headers={"X-Correlation-Id": "c-1"})
    r = client.post(
        "/checkin",
        json={"sample_id": "TS-1042", "freezer_id": "FZ-A"},
        headers={"X-Correlation-Id": "c-2"},
    )
    assert r.status_code == 200
    assert inventory["TS-1042"] == "FZ-A"
    assert scans[-1]["type"] == "checkin" and scans[-1]["correlation_id"] == "c-2"
    assert scans[-1]["location_id"] == "FZ-A" and scans[-1]["reader_id"] == "scanner:reader-fzA"


def test_checkin_guards(ctx):
    client, scans, _ = ctx
    h = {"X-Correlation-Id": "c-3"}
    assert (
        client.post("/checkin", json={"sample_id": "TS-1042", "freezer_id": "FZ-A"}, headers=h)
    ).status_code == 409  # already stored
    client.post("/checkout", json={"sample_id": "TS-1042"}, headers=h)
    n = len(scans)
    bad = client.post("/checkin", json={"sample_id": "TS-1042", "freezer_id": "NOPE"}, headers=h)
    assert bad.status_code == 400 and len(scans) == n


def test_lookup_reports_where_a_sample_is_stored(ctx):
    client, _, _ = ctx
    assert client.get("/samples/TS-1042").json() == {"sample_id": "TS-1042", "location_id": "FZ-B"}
    assert client.get("/samples/TS-9999").status_code == 404


def test_flaky_mode_accepts_the_call_but_emits_no_scan(ctx):
    client, scans, _ = ctx
    client.post("/admin/flaky", json={"drop_scans": True})
    r = client.post("/checkout", json={"sample_id": "TS-1042"}, headers={"X-Correlation-Id": "c-3"})
    assert r.status_code == 200
    assert scans == []  # sent but never confirmed: the gap the verifier must catch

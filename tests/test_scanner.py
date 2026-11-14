import hashlib
import hmac
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from services.scanner.app import MemoryStore, create_app

SECRET = "test-secret"
READERS = {"scanner:reader-fzB": "FZ-B"}
SAMPLES = {"TS-1042"}


def scan(**over):
    body = {
        "reader_id": "scanner:reader-fzB",
        "type": "checkout",
        "sample_id": "TS-1042",
        "location_id": "FZ-B",
        "from_custodian": "location:FZ-B",
        "to_custodian": "courier:CJ-208",
        "correlation_id": "c-7f3a",
    }
    return {**body, **over}


def ctx_pair():
    store = MemoryStore()
    return TestClient(create_app(store, READERS, SAMPLES, SECRET)), store


@pytest.fixture
def ctx():
    store = MemoryStore()
    client = TestClient(create_app(store, READERS, SAMPLES, SECRET))
    return client, store


def test_valid_scan_is_written_with_scanner_id_and_correlation_id(ctx):
    client, store = ctx
    r = client.post("/scan", json=scan())
    assert r.status_code == 201
    [event] = store.events
    assert event["scanner_id"] == "scanner:reader-fzB"
    assert event["correlation_id"] == "c-7f3a"
    assert event["sample_id"] == "TS-1042"


def test_scanner_stamps_the_time_not_the_caller(ctx):
    client, store = ctx
    before = datetime.now(UTC)
    client.post("/scan", json=scan(scanned_at="2001-01-01T00:00:00Z"))
    [event] = store.events
    assert isinstance(event["scanned_at"], datetime)
    assert event["scanned_at"] >= before


def test_signature_verifies_with_the_shared_signer_the_agent_uses():
    from agent.signing import verify_scan

    client, store = ctx_pair()
    client.post("/scan", json=scan())
    [event] = store.events
    assert event["signature"].startswith("hmac-sha256:")
    assert verify_scan(event, SECRET)  # A's verifier accepts what the scanner wrote
    assert not verify_scan({**event, "sample_id": "TS-9999"}, SECRET)  # tampering is caught
    assert not verify_scan(event, "some-other-secret")


@pytest.mark.parametrize(
    "bad",
    [
        {"reader_id": "scanner:rogue"},  # unknown reader
        {"sample_id": "TS-9999"},  # unknown sample
        {"location_id": "SEQ-3"},  # reader is not at that location
        {"type": "teleport"},  # not a scan type
    ],
)
def test_invalid_scans_are_rejected_and_nothing_is_written(ctx, bad):
    client, store = ctx
    r = client.post("/scan", json=scan(**bad))
    assert r.status_code in (400, 404, 422)
    assert store.events == []

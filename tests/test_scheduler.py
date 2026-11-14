import pytest
from fastapi.testclient import TestClient

from services.scheduler.app import create_app


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def ctx():
    clock = Clock()
    return TestClient(create_app(clock=clock, ttl_seconds=60)), clock


def reserve(client, station="SEQ-3", transfer="TR-1"):
    return client.post("/reservations", json={"station_id": station, "transfer_id": transfer})


def test_reserve_returns_held_reservation_with_expiry(ctx):
    client, _ = ctx
    r = reserve(client)
    assert r.status_code == 201
    body = r.json()
    assert body["reservation_id"].startswith("RES-")
    assert body["status"] == "held"
    assert body["expires_at"] == 1060.0


def test_station_already_held_is_a_conflict(ctx):
    client, _ = ctx
    reserve(client)
    assert reserve(client, transfer="TR-2").status_code == 409


def test_other_station_is_independent(ctx):
    client, _ = ctx
    reserve(client)
    assert reserve(client, station="FOR-LAB", transfer="TR-2").status_code == 201


def test_reservation_expires_and_frees_the_station(ctx):
    client, clock = ctx
    rid = reserve(client).json()["reservation_id"]
    clock.t += 61
    assert client.get(f"/reservations/{rid}").json()["status"] == "expired"
    assert reserve(client, transfer="TR-2").status_code == 201


def test_cancel_frees_the_station_and_is_idempotent(ctx):
    client, _ = ctx
    rid = reserve(client).json()["reservation_id"]
    assert client.delete(f"/reservations/{rid}").json()["status"] == "cancelled"
    assert client.delete(f"/reservations/{rid}").status_code == 200
    assert reserve(client, transfer="TR-2").status_code == 201


def test_unknown_reservation_is_404(ctx):
    client, _ = ctx
    assert client.get("/reservations/RES-999").status_code == 404
    assert client.delete("/reservations/RES-999").status_code == 404

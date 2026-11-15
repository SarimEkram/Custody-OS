import asyncio
import json

import httpx
import pytest

from services.config import LabConfig, build_lab_config, http_emit

LOCATIONS = [
    {
        "_id": "FZ-A",
        "name": "Freezer A",
        "kind": "freezer",
        "travel_minutes": {"SEQ-3": 2, "FZ-B": 2},
    },
    {
        "_id": "FZ-B",
        "name": "Freezer B",
        "kind": "freezer",
        "travel_minutes": {"SEQ-3": 4, "FZ-A": 2},
    },
    {
        "_id": "SEQ-3",
        "name": "Sequencing Station 3",
        "kind": "station",
        "travel_minutes": {"FZ-A": 2, "FZ-B": 4},
    },
    {"_id": "CORR-1", "name": "Corridor 1", "kind": "corridor", "travel_minutes": {}},
    {"_id": "FOR-LAB", "name": "Forensics Lab", "kind": "lab", "travel_minutes": {}},
]
SAMPLES = [
    {"_id": "TS-1042", "status": "stored", "location_id": "FZ-B"},
    {"_id": "TS-1077", "status": "stored", "location_id": "FZ-A"},
    {"_id": "EV-2201", "status": "out", "location_id": "SEQ-3"},
]
IDENTITIES = [
    {"_id": "scanner:reader-fza", "kind": "scanner", "location_id": "FZA"},  # seed typo: no hyphen
    {"_id": "scanner:reader-fzb", "kind": "scanner", "location_id": "FZ-B"},  # correct
    {"_id": "scanner:reader-seq3", "kind": "scanner", "location_id": "SEQ3"},
    {"_id": "scanner:reader-forlab", "kind": "scanner", "location_id": "FORLAB"},
    {"_id": "scanner:reader-ghost", "kind": "scanner", "location_id": "NOWHERE"},
    {"_id": "user:jlee", "kind": "human"},
]


def cfg():
    return build_lab_config(LOCATIONS, SAMPLES, IDENTITIES)


def test_freezers_hold_only_samples_that_are_actually_stored():
    assert cfg().inventory == {"TS-1042": "FZ-B", "TS-1077": "FZ-A"}


def test_readers_resolve_to_real_locations_even_with_hyphen_typos_in_the_seed():
    c = cfg()
    assert c.readers == {
        "scanner:reader-fza": "FZ-A",
        "scanner:reader-fzb": "FZ-B",
        "scanner:reader-seq3": "SEQ-3",
        "scanner:reader-forlab": "FOR-LAB",
    }
    assert c.unresolved_readers == ["scanner:reader-ghost"]  # reported, never guessed


def test_location_to_reader_maps_are_split_by_kind():
    c = cfg()
    assert c.freezer_readers == {"FZ-A": "scanner:reader-fza", "FZ-B": "scanner:reader-fzb"}
    assert c.station_readers == {
        "SEQ-3": "scanner:reader-seq3",
        "FOR-LAB": "scanner:reader-forlab",  # kind "lab" is a destination too
    }
    assert c.courier_readers == {
        "FZ-A": "scanner:reader-fza",
        "FZ-B": "scanner:reader-fzb",
        "SEQ-3": "scanner:reader-seq3",
        "FOR-LAB": "scanner:reader-forlab",
    }


def test_travel_table_has_one_entry_per_pair():
    assert cfg().travel == {("FZ-A", "SEQ-3"): 2, ("FZ-A", "FZ-B"): 2, ("FZ-B", "SEQ-3"): 4}


def test_config_survives_a_json_round_trip(tmp_path):
    path = tmp_path / "lab.json"
    path.write_text(json.dumps(cfg().to_json()))
    again = LabConfig.load(path)
    assert again.travel == cfg().travel and again.readers == cfg().readers
    assert again.inventory == cfg().inventory and again.sample_ids == cfg().sample_ids


def _emit(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return http_emit("http://scanner:8105", client=client, pause=0, run=lambda job: job(), **kw)


def test_http_emit_posts_the_raw_scan_to_the_scanner():
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(201, json={})

    _emit(handler)({"reader_id": "r", "type": "checkout"})
    assert seen == [("http://scanner:8105/scan", {"reader_id": "r", "type": "checkout"})]


def test_http_emit_runs_in_the_background_so_the_lab_call_never_waits():
    jobs = []
    emit = http_emit("http://scanner:8105", run=jobs.append)
    emit({"type": "checkout"})  # returns at once: nothing is sent until the job runs
    assert len(jobs) == 1


def test_a_refused_scan_is_logged_not_raised_or_resent(caplog):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(422, json={})

    _emit(handler)({"type": "checkout", "sample_id": "TS-1042"})
    assert len(calls) == 1 and "refused" in caplog.text


@pytest.mark.parametrize("first", ["503", "no_connection"])
def test_a_sleeping_scanner_still_gets_the_scan(first):
    # Render's proxy answers 503 (or the connection fails) while a free-tier scanner wakes
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            if first == "503":
                return httpx.Response(503)
            raise httpx.ConnectError("waking up")
        return httpx.Response(201, json={})

    _emit(handler)({"type": "checkout"})
    assert len(calls) == 2


def test_a_scan_that_may_have_arrived_is_never_sent_twice():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("no answer")

    _emit(handler)({"type": "checkout"})
    assert len(calls) == 1


def test_a_scanner_down_for_good_gives_up_after_its_patience(caplog):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    _emit(handler, patience=0)({"type": "checkout", "sample_id": "TS-1042"})
    assert len(calls) == 1 and "lost" in caplog.text
    assert asyncio  # keep the import used for the async helper below


def test_lab_service_apps_build_from_a_config():
    from services.courier.main import create as courier
    from services.freezer.main import create as freezer
    from services.scheduler.main import create as scheduler
    from services.station.main import create as station

    scans = []
    for factory in (freezer, scheduler, courier, station):
        assert factory(cfg(), scans.append).title


def test_scanner_app_knows_exactly_the_readers_and_samples_in_the_config():
    from fastapi.testclient import TestClient

    from services.scanner.app import MemoryStore
    from services.scanner.main import create

    store = MemoryStore()
    client = TestClient(create(cfg(), store, "secret"))
    body = {
        "reader_id": "scanner:reader-fza",
        "type": "checkout",
        "sample_id": "TS-1077",
        "location_id": "FZ-A",
        "correlation_id": "c-1",
    }
    assert client.post("/scan", json=body).status_code == 201
    assert (
        client.post("/scan", json={**body, "reader_id": "scanner:reader-ghost"}).status_code == 404
    )

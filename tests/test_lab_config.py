import asyncio
import json

import httpx
import pytest

from services.config import LabConfig, build_lab_config, http_emit

LOCATIONS = [
    {"_id": "FZ-A", "name": "Freezer A", "kind": "freezer", "travel_minutes": {"SEQ-3": 2, "FZ-B": 2}},
    {"_id": "FZ-B", "name": "Freezer B", "kind": "freezer", "travel_minutes": {"SEQ-3": 4, "FZ-A": 2}},
    {"_id": "SEQ-3", "name": "Sequencing Station 3", "kind": "station",
     "travel_minutes": {"FZ-A": 2, "FZ-B": 4}},
    {"_id": "CORR-1", "name": "Corridor 1", "kind": "corridor", "travel_minutes": {}},
]  # fmt: skip
SAMPLES = [
    {"_id": "TS-1042", "status": "stored", "location_id": "FZ-B"},
    {"_id": "TS-1077", "status": "stored", "location_id": "FZ-A"},
    {"_id": "EV-2201", "status": "out", "location_id": "SEQ-3"},
]  # fmt: skip
IDENTITIES = [
    {"_id": "scanner:reader-fza", "kind": "scanner", "location_id": "FZA"},  # seed typo: no hyphen
    {"_id": "scanner:reader-fzb", "kind": "scanner", "location_id": "FZ-B"},  # correct
    {"_id": "scanner:reader-seq3", "kind": "scanner", "location_id": "SEQ3"},
    {"_id": "scanner:reader-ghost", "kind": "scanner", "location_id": "NOWHERE"},
    {"_id": "user:jlee", "kind": "human"},
]  # fmt: skip


def cfg():
    return build_lab_config(LOCATIONS, SAMPLES, IDENTITIES)


def test_freezers_hold_only_samples_that_are_actually_stored():
    assert cfg().inventory == {"TS-1042": "FZ-B", "TS-1077": "FZ-A"}


def test_readers_resolve_to_real_locations_even_with_hyphen_typos_in_the_seed():
    c = cfg()
    assert c.readers == {"scanner:reader-fza": "FZ-A", "scanner:reader-fzb": "FZ-B",
                         "scanner:reader-seq3": "SEQ-3"}  # fmt: skip
    assert c.unresolved_readers == ["scanner:reader-ghost"]  # reported, never guessed


def test_location_to_reader_maps_are_split_by_kind():
    c = cfg()
    assert c.freezer_readers == {"FZ-A": "scanner:reader-fza", "FZ-B": "scanner:reader-fzb"}
    assert c.station_readers == {"SEQ-3": "scanner:reader-seq3"}
    assert c.courier_readers == {"FZ-A": "scanner:reader-fza", "FZ-B": "scanner:reader-fzb",
                                 "SEQ-3": "scanner:reader-seq3"}  # fmt: skip


def test_travel_table_has_one_entry_per_pair():
    assert cfg().travel == {("FZ-A", "SEQ-3"): 2, ("FZ-A", "FZ-B"): 2, ("FZ-B", "SEQ-3"): 4}


def test_config_survives_a_json_round_trip(tmp_path):
    path = tmp_path / "lab.json"
    path.write_text(json.dumps(cfg().to_json()))
    again = LabConfig.load(path)
    assert again.travel == cfg().travel and again.readers == cfg().readers
    assert again.inventory == cfg().inventory and again.sample_ids == cfg().sample_ids


def test_http_emit_posts_the_raw_scan_to_the_scanner_and_raises_on_refusal():
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(201 if request.url.path == "/scan" else 404, json={})

    emit = http_emit(
        "http://scanner:8105", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    emit({"reader_id": "r", "type": "checkout"})
    assert seen == [("http://scanner:8105/scan", {"reader_id": "r", "type": "checkout"})]

    refuse = http_emit("http://scanner:8105",
                       client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(422, json={}))))  # fmt: skip
    with pytest.raises(httpx.HTTPStatusError):
        refuse({"x": 1})
    assert asyncio  # keep the import used for the async helper below


def test_lab_service_apps_build_from_a_config():
    from services.courier.main import create as courier
    from services.freezer.main import create as freezer
    from services.scheduler.main import create as scheduler
    from services.station.main import create as station

    scans = []
    for factory in (freezer, scheduler, courier, station):
        assert factory(cfg(), scans.append).title

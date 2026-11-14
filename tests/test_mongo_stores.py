import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from agent.mongo_stores import (
    MongoCustodyStore,
    MongoEventStore,
    find_grant,
    load_catalog,
    load_courier_travel,
    start_scan_feed,
)
from agent.transfers import StateConflict
from agent.verifier import ScanBus
from services.scanner.app import create_app


def run(coro):
    return asyncio.run(coro)


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, n=None):
        return list(self.docs)

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield d

        return gen()


class Coll:
    """Records what the code asked for; returns what the test scripted."""

    def __init__(self, docs=()):
        self.docs, self.finds, self.inserted = list(docs), [], []

    async def find_one(self, flt, *a, **k):
        self.finds.append(flt)
        return self.docs[0] if self.docs else None

    def find(self, flt=None, proj=None, **k):
        self.finds.append(flt)
        return Cursor(self.docs)

    async def insert_one(self, doc, **k):
        self.inserted.append(doc)


class Db:
    def __init__(self, **colls):
        self.__dict__.update(colls)


def test_find_grant_asks_for_an_active_unexpired_grant_from_this_user_to_the_agent():
    grants = Coll([{"_id": "GRANT-0007"}])
    got = run(find_grant(Db(grants=grants), "agent:custody-01")("user:jlee"))
    assert got == "GRANT-0007"
    flt = grants.finds[0]
    assert (flt["grantor"], flt["grantee"], flt["status"]) == (
        "user:jlee",
        "agent:custody-01",
        "active",
    )
    assert "$gt" in flt["expires_at"]  # the TTL monitor lags ~60 s, so the code checks expiry too


def test_no_matching_grant_is_none():
    assert run(find_grant(Db(grants=Coll()), "agent:custody-01")("user:nobody")) is None


def test_catalog_maps_sample_to_where_it_is_and_location_to_its_spoken_name():
    db = Db(samples=Coll([{"_id": "TS-1042", "location_id": "FZ-B"}]),
            locations=Coll([{"_id": "FZ-B", "name": "Freezer B"}]))  # fmt: skip
    cat = run(load_catalog(db))
    assert cat.samples == {"TS-1042": "FZ-B"} and cat.locations == {"FZ-B": "Freezer B"}


def test_courier_travel_table_is_built_once_per_pair_from_the_seeded_locations():
    db = Db(locations=Coll([
        {"_id": "FZ-A", "travel_minutes": {"FZ-B": 2, "SEQ-3": 2}},
        {"_id": "FZ-B", "travel_minutes": {"FZ-A": 2, "SEQ-3": 4}},
    ]))  # fmt: skip
    assert run(load_courier_travel(db)) == {("FZ-A", "FZ-B"): 2, ("FZ-A", "SEQ-3"): 2,
                                           ("FZ-B", "SEQ-3"): 4}  # fmt: skip


def test_physical_states_are_refused_before_the_database_is_touched():
    store = MongoCustodyStore(client=None, db=Db(transfers=Coll()))
    with pytest.raises(StateConflict):
        run(store.set_state("TR-0001", "received"))
    assert store.db.transfers.finds == []


def test_unknown_transfer_and_sample_are_keyerrors():
    store = MongoCustodyStore(client=None, db=Db(transfers=Coll(), samples=Coll()))
    with pytest.raises(KeyError):
        run(store.get_transfer("TR-9999"))
    with pytest.raises(KeyError):
        run(store.get_sample("NOPE"))


def test_the_scanner_can_write_through_an_async_mongo_store():
    events = Coll()
    app = create_app(
        MongoEventStore(Db(custody_events=events)), {"scanner:r": "FZ-B"}, {"TS-1042"}, "s"
    )
    r = TestClient(app).post("/scan", json={"reader_id": "scanner:r", "type": "checkout",
        "sample_id": "TS-1042", "location_id": "FZ-B", "correlation_id": "c-1"})  # fmt: skip
    assert r.status_code == 201
    assert events.inserted[0]["scanner_id"] == "scanner:r" and events.inserted[0]["signature"]


class Stream:
    def __init__(self, changes):
        self.changes = changes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for c in self.changes:
                yield c

        return gen()


def test_scan_feed_hands_every_inserted_custody_event_to_the_bus():
    scan = {"correlation_id": "c-1", "type": "checkout", "scanned_at": datetime.now(UTC)}
    watched = {}

    class Events:
        async def watch(self, pipeline, **kw):
            watched["pipeline"] = pipeline
            return Stream([{"operationType": "insert", "fullDocument": scan}])

    bus = ScanBus()

    async def go():
        await start_scan_feed(Db(custody_events=Events()), bus)

    run(go())
    assert bus.scans["c-1"] == scan
    assert watched["pipeline"] == [{"$match": {"operationType": "insert"}}]

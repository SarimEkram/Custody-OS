import os

import agent.db as db


def test_clients_trust_certifis_ca_bundle_and_fail_fast(monkeypatch):
    seen = {}

    class Fake:
        def __init__(self, uri, **kw):
            seen["uri"], seen["kw"] = uri, kw

    monkeypatch.setattr(db, "AsyncMongoClient", Fake)
    db.client("mongodb+srv://u:p@host/custody")
    assert seen["uri"] == "mongodb+srv://u:p@host/custody"
    assert os.path.isfile(seen["kw"]["tlsCAFile"])  # python.org builds on macOS ship no CA store
    assert 0 < seen["kw"]["serverSelectionTimeoutMS"] <= 10_000
    assert (
        seen["kw"]["tz_aware"] is True
    )  # else Mongo hands back naive datetimes and mixing with UTC-aware ones raises


def test_database_name_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MONGODB_DB", "custody_test")
    assert db.database_name() == "custody_test"
    monkeypatch.delenv("MONGODB_DB")
    assert db.database_name() == "custody"

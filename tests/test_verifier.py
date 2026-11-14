import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.executor import Executor, RunContext, ToolCall
from agent.gate_stub import StubGate
from agent.lab import LabClient
from agent.transfers import MemoryCustodyStore, StateConflict
from agent.verifier import ScanBus, Verifier
from services.courier.app import create_app as courier_app
from services.freezer.app import create_app as freezer_app
from services.scanner.app import create_app as scanner_app
from services.scheduler.app import create_app as scheduler_app
from services.station.app import create_app as station_app

NOW = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


def scan(type_="checkout", location="FZ-B", cid="c-1", sample="TS-1042", to=None):
    return {"scanner_id": "scanner:r", "type": type_, "sample_id": sample, "location_id": location,
            "correlation_id": cid, "scanned_at": NOW, "to_custodian": to}  # fmt: skip


class Ledger:
    def __init__(self):
        self.entries = []

    async def append(self, entry):
        self.entries.append(entry)
        return len(self.entries)


def setup(state="reserved", timeout=0.1):
    store = MemoryCustodyStore(
        samples={"TS-1042": {"status": "stored", "location_id": "FZ-B", "checked_out_at": None}},
        transfers={"TR-1": {"_id": "TR-1", "state": state, "history": []}},
    )
    bus, ledger = ScanBus(), Ledger()
    verifier = Verifier(bus, store, ledger.append, timeout=timeout)
    ctx = RunContext("agent:custody-01", "user:jlee", "GRANT-0007", "TR-1", "VR-1",
                     refs={"sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3"})  # fmt: skip
    return verifier, bus, store, ledger, ctx


def confirm(verifier, ctx, tool="checkout_sample", cid="c-1"):
    return run(verifier.confirm(correlation_id=cid, tool=tool, ctx=ctx, outcome_id=7))


def test_matching_scan_confirms_and_advances_state_and_sample():
    verifier, bus, store, ledger, ctx = setup()
    bus.on_scan(scan())
    assert confirm(verifier, ctx) == "confirmed"
    assert store.transfers["TR-1"]["state"] == "checked_out"
    sample = store.samples["TS-1042"]
    assert sample["status"] == "out" and sample["checked_out_at"] == NOW
    [entry] = ledger.entries
    assert entry["entry_type"] == "verification" and entry["status"] == "confirmed"
    assert entry["refs"] == [7] and entry["correlation_id"] == "c-1"


def test_no_scan_means_unconfirmed_and_state_does_not_move():
    verifier, _, store, ledger, ctx = setup()
    assert confirm(verifier, ctx) == "unconfirmed"
    assert store.transfers["TR-1"]["state"] == "reserved"
    assert ledger.entries[0]["status"] == "unconfirmed"


def test_scan_for_the_wrong_place_is_a_mismatch_with_a_violation():
    verifier, bus, store, ledger, ctx = setup()
    bus.on_scan(scan(location="SEQ-3"))
    assert confirm(verifier, ctx) == "mismatch"
    assert store.transfers["TR-1"]["state"] == "reserved"
    assert [e["entry_type"] for e in ledger.entries] == ["verification", "violation"]


def test_a_scan_that_arrives_before_the_wait_still_counts():
    verifier, bus, *_, ctx = setup()
    bus.on_scan(scan(cid="c-early"))
    assert confirm(verifier, ctx, cid="c-early") == "confirmed"


def test_scan_that_contradicts_the_state_machine_is_a_mismatch():
    verifier, bus, store, _, ctx = setup(state="requested")  # never reserved
    bus.on_scan(scan())
    assert confirm(verifier, ctx) == "mismatch"
    assert store.transfers["TR-1"]["state"] == "requested"


def test_physical_states_can_only_be_set_by_a_verified_scan():
    store = MemoryCustodyStore({}, {"TR-1": {"_id": "TR-1", "state": "requested", "history": []}})
    for physical in ("checked_out", "in_transit", "received", "recovered"):
        with pytest.raises(StateConflict):
            run(store.set_state("TR-1", physical))
    run(store.set_state("TR-1", "blocked"))
    assert store.transfers["TR-1"]["state"] == "blocked"


def test_full_chain_checkout_handoff_intake_updates_custody():
    verifier, bus, store, _, ctx = setup()
    bus.on_scan(scan("checkout", "FZ-B", "c-1"))
    bus.on_scan(scan("handoff", "FZ-B", "c-2", to="courier:CJ-1"))
    bus.on_scan(scan("intake", "SEQ-3", "c-3", to="location:SEQ-3"))
    assert confirm(verifier, ctx, "checkout_sample", "c-1") == "confirmed"
    assert confirm(verifier, ctx, "handoff_to_courier", "c-2") == "confirmed"
    assert confirm(verifier, ctx, "deliver", "c-3") == "confirmed"
    assert store.transfers["TR-1"]["state"] == "received"
    assert [h["state"] for h in store.transfers["TR-1"]["history"]] == [
        "checked_out", "in_transit", "received",
    ]  # fmt: skip
    assert store.samples["TS-1042"]["location_id"] == "SEQ-3"


def test_end_to_end_sent_becomes_confirmed_only_through_the_scanner():
    """Real freezer -> real scanner -> bus -> verifier. The agent never writes the scan."""
    verifier, bus, store, ledger, ctx = setup(timeout=2.0)

    class BusStore:  # stands in for Mongo + change stream: scanner insert -> bus
        def insert(self, event):
            bus.on_scan(event)

    scanner = TestClient(scanner_app(BusStore(), {"scanner:reader-fzB": "FZ-B"}, {"TS-1042"}, "s"))
    emit = lambda s: scanner.post("/scan", json=s).raise_for_status()  # noqa: E731
    apps = {
        "freezer": freezer_app({"TS-1042": "FZ-B"}, emit, {"FZ-B": "scanner:reader-fzB"}),
        "scheduler": scheduler_app(),
        "courier": courier_app({}, {}, emit),
        "station": station_app(emit, {}),
    }
    lab = LabClient(**{
        n: httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://lab")
        for n, a in apps.items()
    })  # fmt: skip
    ledger2 = Ledger()
    ex = Executor(StubGate(ledger2.append), lab, ledger2.append, verifier=verifier)
    res = run(ex.execute(ToolCall(id="t", name="checkout_sample",
                                  args={"sample_id": "TS-1042", "reason": "r"}), ctx))  # fmt: skip
    assert res.status == "OK" and res.verification == "confirmed"
    assert store.transfers["TR-1"]["state"] == "checked_out"
    assert ledger2.entries[1]["status"] == "sent"

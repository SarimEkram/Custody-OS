import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from agent.api import create_app
from agent.confirmation import ConfirmationService, NoPending, NotRequester, readback_text
from agent.parse import Catalog
from agent.signals import Signals
from agent.transfers import MemoryCustodyStore

CATALOG = Catalog({"TS-1042": "FZ-B"}, {"FZ-B": "Freezer B", "SEQ-3": "Sequencing Station 3"})
NOW = datetime(2026, 11, 14, 19, 0, tzinfo=UTC)
TRANSFER = {"_id": "TR-1", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3",
            "requested_by": "user:jlee", "grant_id": "GRANT-0007", "request_id": "VR-1",
            "state": "reserved", "readback_confirmed": False, "history": []}  # fmt: skip


def run(coro):
    return asyncio.run(coro)


class Ledger:
    def __init__(self):
        self.entries = []

    async def append(self, entry):
        self.entries.append(entry)


def build():
    store = MemoryCustodyStore({}, {"TR-1": dict(TRANSFER)})
    ledger, signals, events = Ledger(), Signals(), []
    svc = ConfirmationService(store, ledger.append, signals, events.append, CATALOG,
                              clock=lambda: NOW)  # fmt: skip
    return svc, store, ledger, signals, events


def test_readback_names_the_sample_the_route_and_the_arrival_time():
    text = readback_text(TRANSFER, CATALOG, eta_minutes=4, now=NOW)
    assert text == (
        "I'll check out TS-1042 from Freezer B and send it to Sequencing Station 3, "
        "arriving about 7:04. Confirm?"
    )


def test_signals_work_before_or_after_the_wait_and_time_out():
    s = Signals()
    s.signal("k", "yes")
    assert run(s.wait("k", 0.1)) == "yes"
    assert run(Signals().wait("never", 0.05)) == "timeout"

    async def later():
        sig = Signals()
        asyncio.get_running_loop().call_later(0.02, sig.signal, "k", "approved")
        return await sig.wait("k", 1.0)

    assert run(later()) == "approved"


def test_request_publishes_the_readback_for_the_dashboard():
    svc, _, _, _, events = build()
    out = run(svc.request("TR-1", eta_minutes=4))
    assert out["confirmation_request_id"] == "CONF-1" and out["readback_text"].endswith("Confirm?")
    assert events == [{"kind": "confirmation_request", "data": {
        "id": "CONF-1", "transfer_id": "TR-1", "text": out["readback_text"]}}]  # fmt: skip


def test_confirm_by_the_requester_logs_it_flags_the_transfer_and_wakes_the_agent():
    svc, store, ledger, signals, _ = build()
    out = run(svc.request("TR-1", eta_minutes=4))
    run(svc.confirm("TR-1", "user:jlee", "button"))
    assert store.transfers["TR-1"]["readback_confirmed"] is True
    [e] = ledger.entries
    assert e["entry_type"] == "confirmation" and e["decision"] == "allow"
    assert (e["actor"], e["on_behalf_of"]) == ("user:jlee", "user:jlee")
    assert (e["transfer_id"], e["request_id"], e["grant_id"]) == ("TR-1", "VR-1", "GRANT-0007")
    assert e["response"] == {"readback_text": out["readback_text"], "confirmed_by": "user:jlee",
                             "method": "button", "confirmation_request_id": "CONF-1"}  # fmt: skip
    assert e["correlation_id"]
    assert run(signals.wait("confirm:TR-1", 0.05)) == "confirmed"


def test_nobody_else_can_confirm_for_the_tech():
    svc, store, ledger, *_ = build()
    run(svc.request("TR-1", eta_minutes=4))
    with pytest.raises(NotRequester):
        run(svc.confirm("TR-1", "user:mokafor", "button"))
    assert store.transfers["TR-1"]["readback_confirmed"] is False and ledger.entries == []


def test_confirm_without_a_pending_readback_is_refused():
    svc, *_ = build()
    with pytest.raises(NoPending):
        run(svc.confirm("TR-1", "user:jlee", "button"))


def test_a_readback_can_only_be_confirmed_once():
    svc, *_ = build()
    run(svc.request("TR-1", eta_minutes=4))
    run(svc.confirm("TR-1", "user:jlee", "voice"))
    with pytest.raises(NoPending):
        run(svc.confirm("TR-1", "user:jlee", "voice"))


def test_confirm_endpoint_statuses():
    svc, *_ = build()
    client = TestClient(create_app(intake=None, confirmations=svc))
    body = {"user_id": "user:jlee", "method": "button"}
    assert client.post("/transfers/TR-1/confirm", json=body).status_code == 409  # nothing pending
    run(svc.request("TR-1", eta_minutes=4))
    assert (
        client.post("/transfers/TR-1/confirm", json={**body, "user_id": "user:x"}).status_code
        == 403
    )
    assert (
        client.post("/transfers/TR-1/confirm", json={**body, "method": "telepathy"}).status_code
        == 422
    )
    assert client.post("/transfers/TR-9/confirm", json=body).status_code == 404
    assert client.post("/transfers/TR-1/confirm", json=body).json() == {"confirmed": True}

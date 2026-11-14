import asyncio

import pytest

from agent.transfers import MemoryCustodyStore, StateConflict, next_id


def run(coro):
    return asyncio.run(coro)


def store(**transfers):
    return MemoryCustodyStore(
        {"TS-1042": {"status": "stored", "location_id": "FZ-B"}},
        {k: {"_id": k, "state": "requested", "history": []} for k in transfers},
    )


def test_next_id_continues_from_the_highest_existing_number_in_four_digits():
    assert next_id("TR", []) == "TR-0001"
    assert next_id("TR", ["TR-0001", "TR-0007", "TR-0003"]) == "TR-0008"
    assert next_id("VR", ["TR-0099", "VR-0002"]) == "VR-0003"  # other prefixes are ignored


def test_the_store_hands_out_ids_after_whatever_is_already_there():
    s = store(**{"TR-0001": 1, "TR-0004": 1})
    assert run(s.next_transfer_id()) == "TR-0005"


def test_get_transfer_and_get_sample_raise_keyerror_for_unknown_ids():
    s = store(**{"TR-0001": 1})
    assert run(s.get_transfer("TR-0001"))["state"] == "requested"
    assert run(s.get_sample("TS-1042"))["location_id"] == "FZ-B"
    with pytest.raises(KeyError):
        run(s.get_transfer("TR-9999"))
    with pytest.raises(KeyError):
        run(s.get_sample("NOPE"))


def test_block_remembers_where_it_paused_and_unblock_restores_it_even_if_physical():
    s = store(**{"TR-0001": 1})
    s.transfers["TR-0001"]["state"] = "in_transit"  # reached by a verified scan
    run(s.block("TR-0001", "approval_pending"))
    t = run(s.get_transfer("TR-0001"))
    assert (t["state"], t["blocked_reason"]) == ("blocked", "approval_pending")
    run(s.unblock("TR-0001"))
    t = run(s.get_transfer("TR-0001"))
    assert (t["state"], t["blocked_reason"]) == ("in_transit", None)


def test_confirm_readback_and_mark_compromised_and_create_transfer():
    s = store(**{"TR-0001": 1})
    run(s.confirm_readback("TR-0001"))
    assert s.transfers["TR-0001"]["readback_confirmed"] is True
    run(s.mark_compromised("TS-1042"))
    assert s.samples["TS-1042"]["status"] == "compromised"
    run(s.create_transfer({"_id": "TR-0002", "state": "requested", "history": []}))
    assert "TR-0002" in s.transfers


def test_a_verified_scan_moves_state_and_writes_the_ledger_entry_with_it():
    s = store(**{"TR-0001": 1})
    s.transfers["TR-0001"]["state"] = "reserved"
    written = []

    async def append(entry, **kw):
        written.append(entry)

    scan = {"sample_id": "TS-1042", "type": "checkout", "location_id": "FZ-B", "scanned_at": 1}
    run(s.apply_verified_scan("TR-0001", "reserved", "checked_out", scan, {"id": 1}, append))
    assert s.transfers["TR-0001"]["state"] == "checked_out" and written == [{"id": 1}]
    with pytest.raises(StateConflict):  # wrong expected state: nothing is written
        run(s.apply_verified_scan("TR-0001", "reserved", "checked_out", scan, {"id": 2}, append))
    assert written == [{"id": 1}]

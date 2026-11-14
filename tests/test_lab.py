import asyncio

import httpx
import pytest

from agent.lab import LabClient, LabError
from services.courier.app import create_app as courier_app
from services.freezer.app import create_app as freezer_app
from services.scheduler.app import create_app as scheduler_app
from services.station.app import create_app as station_app


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def build(advance=True, timeout=300.0):
    """advance: the lab client's sleep fast-forwards the courier clock (1 s = 1 simulated min)."""
    scans, clock, slept = [], Clock(), []

    async def sleep(seconds):
        slept.append(seconds)
        if advance:
            clock.t += seconds

    apps = {
        "freezer": freezer_app({"TS-1042": "FZ-B"}, scans.append, {"FZ-B": "scanner:reader-fzB"}),
        "scheduler": scheduler_app(),
        "courier": courier_app(
            {("FZ-B", "SEQ-3"): 4}, {"FZ-B": "scanner:reader-fzB"}, scans.append, clock=clock
        ),
        "station": station_app(scans.append, {"SEQ-3": "scanner:reader-seq3"}),
    }
    clients = {
        name: httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://lab")
        for name, a in apps.items()
    }
    lab = LabClient(**clients, sleep=sleep, arrival_timeout=timeout, poll=1.0)
    lab.slept = slept
    return lab, scans


def run(coro):
    return asyncio.run(coro)


def test_a_full_transfer_drives_the_four_services_with_the_same_correlation_ids():
    lab, scans = build()
    refs = {"transfer_id": "TR-1", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3"}

    async def go():
        assert (await lab.call("lookup_sample", {"sample_id": "TS-1042"}, "c-1", refs))[
            "location_id"
        ] == "FZ-B"
        await lab.call("reserve_station", {"station_id": "SEQ-3"}, "c-2", refs)
        job = await lab.call("request_courier", {"from": "FZ-B", "to": "SEQ-3"}, "c-3", refs)
        assert job["eta_minutes"] == 4
        await lab.call("checkout_sample", {"sample_id": "TS-1042"}, "c-4", refs)
        await lab.call("handoff_to_courier", {"transfer_id": "TR-1"}, "c-5", refs)
        await lab.call("deliver", {"transfer_id": "TR-1"}, "c-6", refs)

    run(go())
    assert refs["reservation_id"] == "RES-1" and refs["courier_job_id"] == "CJ-1"
    assert [(s["type"], s["correlation_id"]) for s in scans] == [
        ("checkout", "c-4"),
        ("handoff", "c-5"),
        ("intake", "c-6"),
    ]
    assert scans[2]["from_custodian"] == "courier:CJ-1"


def test_lab_errors_carry_the_service_status():
    lab, _ = build()
    with pytest.raises(LabError) as e:
        run(lab.call("lookup_sample", {"sample_id": "TS-9999"}, "c-1", {}))
    assert e.value.status_code == 404


def test_compensations_cancel_reservation_and_courier_job():
    lab, _ = build()
    refs = {"transfer_id": "TR-1", "sample_id": "TS-1042"}

    async def go():
        await lab.call("reserve_station", {"station_id": "SEQ-3"}, "c-1", refs)
        await lab.call("request_courier", {"from": "FZ-B", "to": "SEQ-3"}, "c-2", refs)
        r = await lab.call("cancel_reservation", {"reservation_id": "RES-1"}, "c-3", refs)
        j = await lab.call("cancel_courier_job", {"job_id": "CJ-1"}, "c-4", refs)
        return r, j

    r, j = run(go())
    assert r["status"] == "cancelled" and j["status"] == "cancelled"


def test_internal_tools_are_not_lab_calls():
    lab, _ = build()
    with pytest.raises(ValueError):
        run(lab.call("notify", {}, "c-1", {}))


def test_deliver_waits_for_the_courier_to_arrive_before_the_station_intake():
    lab, scans = build()
    refs = {"transfer_id": "TR-1", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3"}

    async def go():
        await lab.call("request_courier", {"from": "FZ-B", "to": "SEQ-3"}, "c-1", refs)
        await lab.call("checkout_sample", {"sample_id": "TS-1042"}, "c-2", refs)
        await lab.call("handoff_to_courier", {"transfer_id": "TR-1"}, "c-3", refs)
        await lab.call("deliver", {"transfer_id": "TR-1"}, "c-4", refs)

    run(go())
    assert len(lab.slept) >= 4  # eta is 4 simulated minutes
    assert scans[-1]["type"] == "intake"


def test_deliver_gives_up_if_the_courier_never_arrives_and_does_not_touch_the_station():
    lab, scans = build(advance=False, timeout=3.0)
    refs = {"transfer_id": "TR-1", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3"}

    async def go():
        await lab.call("request_courier", {"from": "FZ-B", "to": "SEQ-3"}, "c-1", refs)
        await lab.call("checkout_sample", {"sample_id": "TS-1042"}, "c-2", refs)
        await lab.call("handoff_to_courier", {"transfer_id": "TR-1"}, "c-3", refs)
        await lab.call("deliver", {"transfer_id": "TR-1"}, "c-4", refs)

    with pytest.raises(LabError) as e:
        run(go())
    assert e.value.status_code == 409 and "arrive" in e.value.detail
    assert [s["type"] for s in scans] == ["checkout", "handoff"]


def test_return_to_storage_reroutes_a_courier_already_underway_then_checks_in():
    lab, scans = build()
    refs = {"transfer_id": "TR-1", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3"}

    async def go():
        await lab.call("request_courier", {"from": "FZ-B", "to": "SEQ-3"}, "c-1", refs)
        await lab.call("checkout_sample", {"sample_id": "TS-1042"}, "c-2", refs)
        await lab.call("handoff_to_courier", {"transfer_id": "TR-1"}, "c-3", refs)
        await lab.sleep(2)  # halfway to the station
        return await lab.call(
            "return_to_storage", {"sample_id": "TS-1042", "freezer_id": "FZ-B"}, "c-4", refs
        )

    run(go())
    assert [(s["type"], s["correlation_id"]) for s in scans][-1] == ("checkin", "c-4")
    assert scans[-1]["location_id"] == "FZ-B"


def test_return_to_storage_before_pickup_just_checks_the_sample_back_in():
    lab, scans = build()
    refs = {"transfer_id": "TR-1", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3"}

    async def go():
        await lab.call("request_courier", {"from": "FZ-B", "to": "SEQ-3"}, "c-1", refs)
        await lab.call("checkout_sample", {"sample_id": "TS-1042"}, "c-2", refs)
        await lab.call(
            "return_to_storage", {"sample_id": "TS-1042", "freezer_id": "FZ-B"}, "c-3", refs
        )

    run(go())
    assert [s["type"] for s in scans] == ["checkout", "checkin"] and not lab.slept

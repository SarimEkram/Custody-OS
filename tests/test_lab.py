import asyncio

import httpx
import pytest

from agent.lab import LabClient, LabError
from services.courier.app import create_app as courier_app
from services.freezer.app import create_app as freezer_app
from services.scheduler.app import create_app as scheduler_app
from services.station.app import create_app as station_app


def build():
    scans = []
    apps = {
        "freezer": freezer_app({"TS-1042": "FZ-B"}, scans.append, {"FZ-B": "scanner:reader-fzB"}),
        "scheduler": scheduler_app(),
        "courier": courier_app(
            {("FZ-B", "SEQ-3"): 4}, {"FZ-B": "scanner:reader-fzB"}, scans.append
        ),
        "station": station_app(scans.append, {"SEQ-3": "scanner:reader-seq3"}),
    }
    clients = {
        name: httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://lab")
        for name, a in apps.items()
    }
    return LabClient(**clients), scans


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

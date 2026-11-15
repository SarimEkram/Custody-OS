"""HTTP adapter from tool names to the four mock lab systems.

`refs` is the transfer's working memory (transfer_id, sample_id, from, to, plus ids the
lab hands back: reservation_id, courier_job_id). Internal tools (notify, parse_request,
request_confirmation) are not lab calls and are rejected here.
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx


class LabError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class LabClient:
    def __init__(
        self,
        freezer: httpx.AsyncClient,
        scheduler: httpx.AsyncClient,
        courier: httpx.AsyncClient,
        station: httpx.AsyncClient,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        arrival_timeout: float = 300.0,
        poll: float = 0.5,
        wake_timeout: float = 60.0,
        wake_pause: float = 3.0,
    ) -> None:
        self.freezer, self.scheduler, self.courier, self.station = (
            freezer,
            scheduler,
            courier,
            station,
        )
        self.sleep, self.arrival_timeout, self.poll = sleep, arrival_timeout, poll
        self.wake_timeout, self.wake_pause = wake_timeout, wake_pause

    async def _wait_for_arrival(self, job: str, cid: str) -> None:
        """deliver must not touch the station before the courier is there; the LLM must not poll."""
        waited = 0.0
        while (await self._send(self.courier, "GET", f"/jobs/{job}", cid))["status"] != "arrived":
            if waited >= self.arrival_timeout:
                raise LabError(409, f"courier {job} did not arrive within {self.arrival_timeout}s")
            await self.sleep(self.poll)
            waited += self.poll

    async def _send(self, client: httpx.AsyncClient, method: str, url: str, cid: str, **kw) -> dict:
        # A sleeping free-tier host refuses the connection, or its proxy answers 502/503, while it
        # boots. That request never reached the service, so waiting and resending is safe. (Not 504:
        # the service may have acted, and a checkout must never run twice.)
        waited = 0.0
        while True:
            try:
                r = await client.request(method, url, headers={"X-Correlation-Id": cid}, **kw)
                if r.status_code not in (502, 503) or waited >= self.wake_timeout:
                    break
            except httpx.ConnectError:
                if waited >= self.wake_timeout:
                    raise
            await self.sleep(self.wake_pause)
            waited += self.wake_pause
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:  # e.g. the host's HTML error page, not the service's JSON
                detail = r.text[:200] or r.reason_phrase
            raise LabError(r.status_code, detail)
        return r.json()

    async def call(self, tool: str, args: dict, correlation_id: str, refs: dict) -> dict:
        cid = correlation_id
        job = refs.get("courier_job_id")
        match tool:
            case "lookup_sample":
                return await self._send(self.freezer, "GET", f"/samples/{args['sample_id']}", cid)
            case "reserve_station":
                res = await self._send(
                    self.scheduler, "POST", "/reservations", cid,
                    json={"station_id": args["station_id"], "transfer_id": refs["transfer_id"]},
                )  # fmt: skip
                refs["reservation_id"] = res["reservation_id"]
                return res
            case "request_courier":
                res = await self._send(
                    self.courier, "POST", "/jobs", cid,
                    json={"sample_id": refs["sample_id"], "origin": args["from"],
                          "destination": args["to"], "transfer_id": refs["transfer_id"]},
                )  # fmt: skip
                refs["courier_job_id"] = res["job_id"]
                refs["eta_minutes"] = res["eta_minutes"]
                return res
            case "checkout_sample":
                body = {"sample_id": args["sample_id"]}
                if job:
                    body["to_custodian"] = f"courier:{job}"
                return await self._send(self.freezer, "POST", "/checkout", cid, json=body)
            case "handoff_to_courier":
                return await self._send(self.courier, "POST", f"/jobs/{job}/pickup", cid)
            case "deliver":
                await self._wait_for_arrival(job, cid)
                return await self._send(
                    self.station, "POST", "/intake", cid,
                    json={"sample_id": refs["sample_id"], "station_id": refs["to"],
                          "from_custodian": f"courier:{job}"},
                )  # fmt: skip
            case "return_to_storage":
                freezer = args["freezer_id"]
                if job and (await self._send(self.courier, "GET", f"/jobs/{job}", cid))[
                    "status"
                ] in ("in_transit", "arrived"):
                    await self._send(
                        self.courier, "POST", f"/jobs/{job}/reroute", cid,
                        json={"destination": freezer},
                    )  # fmt: skip
                    await self._wait_for_arrival(job, cid)
                return await self._send(
                    self.freezer, "POST", "/checkin", cid,
                    json={"sample_id": args["sample_id"], "freezer_id": freezer},
                )  # fmt: skip
            case "cancel_reservation":
                return await self._send(
                    self.scheduler, "DELETE", f"/reservations/{args['reservation_id']}", cid
                )
            case "cancel_courier_job":
                return await self._send(self.courier, "DELETE", f"/jobs/{args['job_id']}", cid)
        raise ValueError(f"{tool} is not a lab call")

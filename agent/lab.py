"""HTTP adapter from tool names to the four mock lab systems.

`refs` is the transfer's working memory (transfer_id, sample_id, from, to, plus ids the
lab hands back: reservation_id, courier_job_id). Internal tools (notify, parse_request,
request_confirmation) are not lab calls and are rejected here.
"""

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
    ) -> None:
        self.freezer, self.scheduler, self.courier, self.station = (
            freezer,
            scheduler,
            courier,
            station,
        )

    async def _send(self, client: httpx.AsyncClient, method: str, url: str, cid: str, **kw) -> dict:
        r = await client.request(method, url, headers={"X-Correlation-Id": cid}, **kw)
        if r.status_code >= 400:
            raise LabError(r.status_code, r.json().get("detail", r.text))
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
                return res
            case "checkout_sample":
                body = {"sample_id": args["sample_id"]}
                if job:
                    body["to_custodian"] = f"courier:{job}"
                return await self._send(self.freezer, "POST", "/checkout", cid, json=body)
            case "handoff_to_courier":
                return await self._send(self.courier, "POST", f"/jobs/{job}/pickup", cid)
            case "deliver":
                return await self._send(
                    self.station, "POST", "/intake", cid,
                    json={"sample_id": refs["sample_id"], "station_id": refs["to"],
                          "from_custodian": f"courier:{job}"},
                )  # fmt: skip
            case "cancel_reservation":
                return await self._send(
                    self.scheduler, "DELETE", f"/reservations/{args['reservation_id']}", cid
                )
            case "cancel_courier_job":
                return await self._send(self.courier, "DELETE", f"/jobs/{args['job_id']}", cid)
        raise ValueError(f"{tool} is not a lab call")

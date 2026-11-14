"""Mock scheduler: station reservations with a TTL. Reversible (cancel_reservation).

Holds no scans: nothing physical happens when a station is booked.
"""

import time
from collections.abc import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class ReserveBody(BaseModel):
    station_id: str
    transfer_id: str


def create_app(clock: Callable[[], float] = time.time, ttl_seconds: int = 1800) -> FastAPI:
    app = FastAPI(title="scheduler")
    reservations: dict[str, dict] = {}

    def view(res: dict) -> dict:
        status = res["status"]
        if status == "held" and clock() >= res["expires_at"]:
            status = "expired"
        return {**res, "status": status}

    @app.post("/reservations", status_code=201)
    def reserve(body: ReserveBody) -> dict:
        for res in reservations.values():
            if res["station_id"] == body.station_id and view(res)["status"] == "held":
                raise HTTPException(409, f"{body.station_id} already held by {res['transfer_id']}")
        rid = f"RES-{len(reservations) + 1}"
        reservations[rid] = {
            "reservation_id": rid,
            "station_id": body.station_id,
            "transfer_id": body.transfer_id,
            "status": "held",
            "expires_at": clock() + ttl_seconds,
        }
        return view(reservations[rid])

    def get_or_404(rid: str) -> dict:
        if rid not in reservations:
            raise HTTPException(404, f"unknown reservation {rid}")
        return reservations[rid]

    @app.get("/reservations/{rid}")
    def get(rid: str) -> dict:
        return view(get_or_404(rid))

    @app.delete("/reservations/{rid}")
    def cancel(rid: str) -> dict:
        res = get_or_404(rid)
        if view(res)["status"] == "held":
            res["status"] = "cancelled"
        return view(res)

    return app

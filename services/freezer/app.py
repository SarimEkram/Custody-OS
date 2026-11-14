"""Mock freezer system. Owns its own inventory; never writes custody_events.

When a sample physically leaves or returns, it hands a raw scan to `emit`
(an HTTP post to the scanner in production). `drop_scans` simulates a reader
that fails, so the agent's "sent" is never confirmed.
"""
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


class SampleBody(BaseModel):
    sample_id: str
    to_custodian: str | None = None


class Flaky(BaseModel):
    drop_scans: bool = False


def create_app(inventory: dict[str, str], emit: Callable[[dict], None],
               reader_id: str, location_id: str) -> FastAPI:
    app = FastAPI(title="freezer")
    state = {"drop_scans": False}

    def scan(kind: str, body: SampleBody, correlation_id: str, **custody) -> bool:
        if state["drop_scans"]:
            return False
        try:
            emit({"reader_id": reader_id, "type": kind, "sample_id": body.sample_id,
                  "location_id": location_id, "correlation_id": correlation_id, **custody})
            return True
        except Exception:  # scanner unreachable: the call still happened, it is just unconfirmed
            return False

    def need_correlation(cid: str | None) -> str:
        if not cid:
            raise HTTPException(400, "X-Correlation-Id header required")
        return cid

    @app.post("/checkout")
    def checkout(body: SampleBody, x_correlation_id: str | None = Header(None)) -> dict:
        cid = need_correlation(x_correlation_id)
        if inventory.get(body.sample_id) != location_id:
            raise HTTPException(404, f"{body.sample_id} is not stored here")
        del inventory[body.sample_id]
        emitted = scan("checkout", body, cid, from_custodian=f"location:{location_id}",
                       to_custodian=body.to_custodian)
        return {"correlation_id": cid, "scan_emitted": emitted}

    @app.post("/checkin")
    def checkin(body: SampleBody, x_correlation_id: str | None = Header(None)) -> dict:
        cid = need_correlation(x_correlation_id)
        if body.sample_id in inventory:
            raise HTTPException(409, f"{body.sample_id} is already stored")
        inventory[body.sample_id] = location_id
        emitted = scan("checkin", body, cid, to_custodian=f"location:{location_id}")
        return {"correlation_id": cid, "scan_emitted": emitted}

    @app.get("/inventory")
    def get_inventory() -> dict:
        return dict(inventory)

    @app.post("/admin/flaky")
    def flaky(body: Flaky) -> dict:
        state["drop_scans"] = body.drop_scans
        return state

    return app

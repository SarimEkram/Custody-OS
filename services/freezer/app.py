"""Mock freezer system, holding every freezer (FZ-A, FZ-B, ...). Never writes custody_events.

`inventory` maps sample -> the freezer that stores it; a sample that is out has no entry.
Each freezer has its own reader, so scans carry the right location. When a sample leaves
or returns it hands a raw scan to `emit` (an HTTP post to the scanner in production).
`drop_scans` simulates a dead reader, so the agent's "sent" is never confirmed.
"""

from collections.abc import Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from services.common import Emitter, need_correlation


class CheckoutBody(BaseModel):
    sample_id: str
    to_custodian: str | None = None


class CheckinBody(BaseModel):
    sample_id: str
    freezer_id: str


class Flaky(BaseModel):
    drop_scans: bool = False


def create_app(
    inventory: dict[str, str], emit: Callable[[dict], None], readers: dict[str, str]
) -> FastAPI:
    """readers: freezer location -> reader_id."""
    app = FastAPI(title="freezer")
    scanner = Emitter(emit)

    @app.post("/checkout")
    def checkout(body: CheckoutBody, x_correlation_id: str | None = Header(None)) -> dict:
        cid = need_correlation(x_correlation_id)
        if body.sample_id not in inventory:
            raise HTTPException(404, f"{body.sample_id} is not stored in any freezer")
        location = inventory.pop(body.sample_id)
        emitted = scanner.send(
            readers[location],
            "checkout",
            body.sample_id,
            location,
            cid,
            from_custodian=f"location:{location}",
            to_custodian=body.to_custodian,
        )
        return {"correlation_id": cid, "scan_emitted": emitted}

    @app.post("/checkin")
    def checkin(body: CheckinBody, x_correlation_id: str | None = Header(None)) -> dict:
        cid = need_correlation(x_correlation_id)
        if body.freezer_id not in readers:
            raise HTTPException(400, f"unknown freezer {body.freezer_id}")
        if body.sample_id in inventory:
            raise HTTPException(409, f"{body.sample_id} is already stored")
        inventory[body.sample_id] = body.freezer_id
        emitted = scanner.send(
            readers[body.freezer_id],
            "checkin",
            body.sample_id,
            body.freezer_id,
            cid,
            to_custodian=f"location:{body.freezer_id}",
        )
        return {"correlation_id": cid, "scan_emitted": emitted}

    @app.get("/samples/{sample_id}")
    def lookup(sample_id: str) -> dict:
        if sample_id not in inventory:
            raise HTTPException(404, f"{sample_id} is not stored in any freezer")
        return {"sample_id": sample_id, "location_id": inventory[sample_id]}

    @app.get("/inventory")
    def get_inventory() -> dict:
        return dict(inventory)

    @app.post("/admin/flaky")
    def flaky(body: Flaky) -> dict:
        scanner.drop = body.drop_scans
        return {"drop_scans": scanner.drop}

    return app

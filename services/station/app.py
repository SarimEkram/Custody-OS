"""Mock receiving stations (SEQ-3, FOR-LAB, ...). Intake is physical: it emits an `intake`
scan, nothing more. Each station has its own reader so scans carry the right location."""

from collections.abc import Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from services.common import Emitter, need_correlation


class IntakeBody(BaseModel):
    sample_id: str
    station_id: str
    from_custodian: str | None = None


class Flaky(BaseModel):
    drop_scans: bool = False


def create_app(emit: Callable[[dict], None], readers: dict[str, str]) -> FastAPI:
    """readers: station location -> reader_id."""
    app = FastAPI(title="station")
    scanner = Emitter(emit)
    received: dict[str, list[str]] = {station: [] for station in readers}

    @app.post("/intake")
    def intake(body: IntakeBody, x_correlation_id: str | None = Header(None)) -> dict:
        cid = need_correlation(x_correlation_id)
        if body.station_id not in readers:
            raise HTTPException(400, f"unknown station {body.station_id}")
        if any(body.sample_id in samples for samples in received.values()):
            raise HTTPException(409, f"{body.sample_id} already received")
        received[body.station_id].append(body.sample_id)
        emitted = scanner.send(
            readers[body.station_id],
            "intake",
            body.sample_id,
            body.station_id,
            cid,
            from_custodian=body.from_custodian,
            to_custodian=f"location:{body.station_id}",
        )
        return {"correlation_id": cid, "scan_emitted": emitted}

    @app.get("/status")
    def status() -> dict:
        return {"received": {station: list(s) for station, s in received.items()}}

    @app.post("/admin/flaky")
    def flaky(body: Flaky) -> dict:
        scanner.drop = body.drop_scans
        return {"drop_scans": scanner.drop}

    return app

"""Mock receiving station. Intake is physical: it emits an `intake` scan, nothing more."""

from collections.abc import Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from services.common import Emitter, need_correlation


class IntakeBody(BaseModel):
    sample_id: str
    from_custodian: str | None = None


class Flaky(BaseModel):
    drop_scans: bool = False


def create_app(emit: Callable[[dict], None], reader_id: str, location_id: str) -> FastAPI:
    app = FastAPI(title="station")
    scanner = Emitter(emit)
    received: list[str] = []

    @app.post("/intake")
    def intake(body: IntakeBody, x_correlation_id: str | None = Header(None)) -> dict:
        cid = need_correlation(x_correlation_id)
        if body.sample_id in received:
            raise HTTPException(409, f"{body.sample_id} already received")
        received.append(body.sample_id)
        emitted = scanner.send(
            reader_id,
            "intake",
            body.sample_id,
            location_id,
            cid,
            from_custodian=body.from_custodian,
            to_custodian=f"location:{location_id}",
        )
        return {"correlation_id": cid, "scan_emitted": emitted}

    @app.get("/status")
    def status() -> dict:
        return {"location_id": location_id, "received": list(received)}

    @app.post("/admin/flaky")
    def flaky(body: Flaky) -> dict:
        scanner.drop = body.drop_scans
        return {"drop_scans": scanner.drop}

    return app

"""Scanner service: the only writer of custody_events (rule 3).

Lab services post raw scans here. The scanner checks them, stamps the time itself,
signs the event, and stores it. The store is injected: MemoryStore for now, a
Mongo-backed one (scanner_svc user) once the Atlas URIs exist.
"""

import inspect
from datetime import UTC, datetime
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent.signing import sign_scan  # one signing formula, shared with the verifier


class RawScan(BaseModel):
    reader_id: str
    type: Literal["checkout", "handoff", "intake", "checkin", "seal_check"]
    sample_id: str
    location_id: str
    from_custodian: str | None = None
    to_custodian: str | None = None
    correlation_id: str
    seal_id: str | None = None


class Store(Protocol):
    def insert(self, event: dict) -> object: ...


class MemoryStore:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def insert(self, event: dict) -> int:
        self.events.append(event)
        return len(self.events)


def create_app(store: Store, readers: dict[str, str], samples: set[str], secret: str) -> FastAPI:
    """readers maps reader_id -> the location it is installed at."""
    app = FastAPI(title="scanner")

    @app.post("/scan", status_code=201)
    async def scan(raw: RawScan) -> dict:
        if raw.reader_id not in readers:
            raise HTTPException(404, f"unknown reader {raw.reader_id}")
        if raw.sample_id not in samples:
            raise HTTPException(404, f"unknown sample {raw.sample_id}")
        if readers[raw.reader_id] != raw.location_id:
            raise HTTPException(400, f"{raw.reader_id} is not at {raw.location_id}")
        event = raw.model_dump(exclude={"reader_id"}, exclude_none=True)
        event["scanner_id"] = raw.reader_id
        event["scanned_at"] = datetime.now(UTC)
        event["signature"] = sign_scan(event, secret)
        written = store.insert(event)
        if inspect.isawaitable(written):  # the Mongo store is async, the memory one is not
            await written
        return {"correlation_id": raw.correlation_id, "scanned_at": event["scanned_at"].isoformat()}

    return app

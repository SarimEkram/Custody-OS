"""The scanner's Mongo store: the only code that writes custody_events (scanner_svc user)."""

from typing import Any


class MongoEventStore:
    """The scanner's store: the only writer of custody_events (scanner_svc user)."""

    def __init__(self, db) -> None:
        self.db = db

    async def insert(self, event: dict) -> Any:
        return await self.db.custody_events.insert_one(event)

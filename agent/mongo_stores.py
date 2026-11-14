"""Atlas-backed implementations of the stores the agent uses (agent_svc user, agentRole).

The agent can insert/update transfers, samples and approvals, but only insert voice_requests
and GridFS audio, and never touches custody_events (rule 3) or updates the ledger (rule 4):
the ledger is A's agent/ledger.py. Everything here takes `db` so tests and the scratch-database
smoke script can point it elsewhere.
"""

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from gridfs.asynchronous import AsyncGridFSBucket

from agent.parse import Catalog
from agent.transfers import (
    PHYSICAL,
    Append,
    StateConflict,
    TransferState,
    apply_scan_to_sample,
    next_id,
)


def _now() -> datetime:
    return datetime.now(UTC)


class MongoCustodyStore:
    def __init__(self, client, db) -> None:
        self.client, self.db = client, db

    async def _ids(self, collection) -> list[str]:
        return [d["_id"] for d in await collection.find({}, {"_id": 1}).to_list()]

    async def next_transfer_id(self) -> str:
        return next_id("TR", await self._ids(self.db.transfers))

    async def create_transfer(self, doc: dict) -> None:
        await self.db.transfers.insert_one(doc)

    async def get_transfer(self, transfer_id: str) -> dict:
        doc = await self.db.transfers.find_one({"_id": transfer_id})
        if doc is None:
            raise KeyError(transfer_id)
        return doc

    async def get_sample(self, sample_id: str) -> dict:
        doc = await self.db.samples.find_one({"_id": sample_id})
        if doc is None:
            raise KeyError(sample_id)
        return doc

    async def _move(self, transfer_id: str, state: str, extra: dict | None = None, **kw) -> None:
        now = _now()
        await self.db.transfers.update_one(
            {"_id": transfer_id},
            {"$set": {"state": str(state), "updated_at": now, **(extra or {})},
             "$push": {"history": {"state": str(state), "at": now}}},
            **kw,
        )  # fmt: skip

    async def set_state(self, transfer_id: str, state: str) -> None:
        if state in PHYSICAL:
            raise StateConflict(f"{state} can only be set by a verified scan")
        await self._move(transfer_id, state)

    async def block(self, transfer_id: str, reason: str) -> None:
        current = (await self.get_transfer(transfer_id))["state"]
        await self._move(transfer_id, TransferState.BLOCKED,
                         {"blocked_from": current, "blocked_reason": reason})  # fmt: skip

    async def unblock(self, transfer_id: str) -> None:
        back = (await self.get_transfer(transfer_id))["blocked_from"]
        await self._move(transfer_id, back, {"blocked_reason": None})

    async def confirm_readback(self, transfer_id: str) -> None:
        await self.db.transfers.update_one(
            {"_id": transfer_id}, {"$set": {"readback_confirmed": True, "updated_at": _now()}}
        )

    async def mark_compromised(self, sample_id: str) -> None:
        await self.db.samples.update_one({"_id": sample_id}, {"$set": {"status": "compromised"}})

    async def apply_verified_scan(
        self,
        transfer_id: str,
        expect: str,
        next_state: str,
        scan: dict,
        ledger_entry: dict | None = None,
        append: Append | None = None,
    ) -> None:
        """One transaction: sample custody, transfer state and the verification ledger entry."""
        now = _now()
        async with self.client.start_session() as session:
            async with await session.start_transaction():
                moved = await self.db.transfers.update_one(
                    {"_id": transfer_id, "state": expect},  # optimistic guard
                    {"$set": {"state": next_state, "updated_at": now},
                     "$push": {"history": {"state": next_state, "at": now,
                                           "scan_id": scan.get("_id")}}},
                    session=session,
                )  # fmt: skip
                if moved.modified_count != 1:
                    raise StateConflict(f"{transfer_id} is not {expect}")
                await self.db.samples.update_one(
                    {"_id": scan["sample_id"]},
                    {"$set": apply_scan_to_sample({}, scan)},
                    session=session,
                )
                if append and ledger_entry:
                    await append(ledger_entry, session=session)  # A's ledger.append takes session


class MongoRequests:
    """voice_requests is insert-only for the agent (no UPDATE privilege)."""

    def __init__(self, db) -> None:
        self.db = db

    async def next_id(self) -> str:
        ids = [d["_id"] for d in await self.db.voice_requests.find({}, {"_id": 1}).to_list()]
        return next_id("VR", ids)

    async def insert(self, doc: dict) -> None:
        await self.db.voice_requests.insert_one(doc)


class MongoAudio:
    """GridFS bucket `voice_audio`; the SHA-256 is stored on the voice_requests doc."""

    def __init__(self, db) -> None:
        self.bucket = AsyncGridFSBucket(db, bucket_name="voice_audio")

    async def put(self, data: bytes, filename: str):
        return await self.bucket.upload_from_stream(filename, data)

    async def get(self, file_id) -> bytes:
        stream = await self.bucket.open_download_stream(file_id)
        return await stream.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_grant(db, agent_id: str) -> Callable[[str], Awaitable[str | None]]:
    """An active, unexpired grant from this user to the agent. TTL lags ~60 s, so check here."""

    async def lookup(user_id: str) -> str | None:
        doc = await db.grants.find_one({
            "grantor": user_id, "grantee": agent_id, "status": "active",
            "expires_at": {"$gt": _now()},
        })  # fmt: skip
        return doc["_id"] if doc else None

    return lookup


async def load_catalog(db) -> Catalog:
    samples = {d["_id"]: d["location_id"] for d in await db.samples.find({}).to_list()}
    locations = {d["_id"]: d["name"] for d in await db.locations.find({}).to_list()}
    return Catalog(samples=samples, locations=locations)


async def load_courier_travel(db) -> dict[tuple[str, str], float]:
    """Pairwise minutes from locations.travel_minutes, one entry per pair."""
    travel: dict[tuple[str, str], float] = {}
    for loc in await db.locations.find({}).to_list():
        for other, minutes in loc.get("travel_minutes", {}).items():
            if (other, loc["_id"]) not in travel:
                travel[(loc["_id"], other)] = minutes
    return travel


async def start_scan_feed(db, bus) -> None:
    """Feed every inserted custody_event to the verifier's ScanBus. Runs until the stream closes;
    wrap in asyncio.create_task. A's watcher hook can replace this when it lands.

    shortcut: no resume token, so scans inserted while the agent was down are missed; the
    verifier then times out as `unconfirmed`, which is the safe outcome.
    """
    stream = await db.custody_events.watch([{"$match": {"operationType": "insert"}}])
    async with stream:
        async for change in stream:
            bus.on_scan(change["fullDocument"])

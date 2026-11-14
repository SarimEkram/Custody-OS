"""Transfer states and the custody store (transfers + samples).

Physical states (checked_out, in_transit, received, recovered) are only ever set by
apply_verified_scan(), i.e. after the scanner confirmed (rule 3). The store interface is async
because the real one talks to Atlas; MemoryCustodyStore is the in-memory implementation used
by unit tests and offline demos, MongoCustodyStore (agent/mongo_stores.py) the real one.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class TransferState(StrEnum):
    REQUESTED = "requested"
    RESERVED = "reserved"
    CHECKED_OUT = "checked_out"
    IN_TRANSIT = "in_transit"
    RECEIVED = "received"
    BLOCKED = "blocked"
    REVOKED = "revoked"
    RECOVERING = "recovering"
    RECOVERED = "recovered"
    CANCELLED = "cancelled"


PHYSICAL = {
    TransferState.CHECKED_OUT, TransferState.IN_TRANSIT,
    TransferState.RECEIVED, TransferState.RECOVERED,
}  # fmt: skip


class StateConflict(Exception):
    pass


def next_id(prefix: str, existing) -> str:
    """TR-0001 style ids, continuing after the highest one already stored."""
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    numbers = [int(m.group(1)) for i in existing if (m := pattern.match(str(i)))]
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"


Append = Callable[..., Awaitable[Any]]


def apply_scan_to_sample(sample: dict, scan: dict) -> dict:
    """The sample fields a verified scan changes (shared by every store implementation)."""
    changes = {"location_id": scan["location_id"], "custodian": scan.get("to_custodian")}
    if scan["type"] == "checkout":
        changes.update(status="out", checked_out_at=scan["scanned_at"])
    elif scan["type"] == "checkin":
        changes.update(status="stored", checked_out_at=None)
    return changes


class MemoryCustodyStore:
    def __init__(self, samples: dict[str, dict], transfers: dict[str, dict]) -> None:
        self.samples, self.transfers = samples, transfers

    async def next_transfer_id(self) -> str:
        return next_id("TR", self.transfers)

    async def create_transfer(self, doc: dict) -> None:
        self.transfers[doc["_id"]] = doc

    async def get_transfer(self, transfer_id: str) -> dict:
        return self.transfers[transfer_id]

    async def get_sample(self, sample_id: str) -> dict:
        return self.samples[sample_id]

    async def confirm_readback(self, transfer_id: str) -> None:
        self.transfers[transfer_id]["readback_confirmed"] = True

    async def set_fields(self, transfer_id: str, **fields) -> None:
        self.transfers[transfer_id].update(fields)

    async def mark_compromised(self, sample_id: str) -> None:
        self.samples[sample_id]["status"] = "compromised"

    async def block(self, transfer_id: str, reason: str) -> None:
        """Pause for approval or read-back; remembers the verified state it paused in."""
        transfer = self.transfers[transfer_id]
        transfer["blocked_from"], transfer["blocked_reason"] = transfer["state"], reason
        self._move(transfer_id, TransferState.BLOCKED)

    async def unblock(self, transfer_id: str) -> None:
        """Go back to the state it paused in (already scan-verified, so allowed)."""
        transfer = self.transfers[transfer_id]
        transfer["blocked_reason"] = None
        self._move(transfer_id, transfer["blocked_from"])

    async def set_state(self, transfer_id: str, state: str) -> None:
        if state in PHYSICAL:
            raise StateConflict(f"{state} can only be set by a verified scan")
        self._move(transfer_id, state)

    async def apply_verified_scan(
        self,
        transfer_id: str,
        expect: str,
        next_state: str,
        scan: dict,
        ledger_entry: dict | None = None,
        append: Append | None = None,
    ) -> None:
        transfer = self.transfers[transfer_id]
        if transfer["state"] != expect:
            raise StateConflict(f"{transfer_id} is {transfer['state']}, expected {expect}")
        sample = self.samples[scan["sample_id"]]
        sample.update(apply_scan_to_sample(sample, scan))
        self._move(transfer_id, next_state)
        if append and ledger_entry:
            await append(ledger_entry)

    def _move(self, transfer_id: str, state: str) -> None:
        transfer = self.transfers[transfer_id]
        transfer["state"] = str(state)
        transfer["updated_at"] = datetime.now(UTC)
        transfer["history"].append({"state": str(state), "at": transfer["updated_at"]})

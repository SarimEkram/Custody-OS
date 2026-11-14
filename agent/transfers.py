"""Transfer states and the custody store (transfers + samples).

Physical states (checked_out, in_transit, received, recovered) are only ever set by
apply_verified_scan(), i.e. after the scanner confirmed (rule 3). MemoryCustodyStore is the
in-memory stand-in; the Mongo version must do the same writes inside one transaction
(DATA_MODEL.md "Transaction: verified custody handoff"), including the ledger entry.
"""

from datetime import UTC, datetime
from enum import StrEnum


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


class MemoryCustodyStore:
    def __init__(self, samples: dict[str, dict], transfers: dict[str, dict]) -> None:
        self.samples, self.transfers = samples, transfers

    def next_transfer_id(self) -> str:
        return f"TR-{len(self.transfers) + 1}"

    def create_transfer(self, doc: dict) -> None:
        self.transfers[doc["_id"]] = doc

    def block(self, transfer_id: str, reason: str) -> None:
        """Pause for approval or read-back; remembers the verified state it paused in."""
        transfer = self.transfers[transfer_id]
        transfer["blocked_from"], transfer["blocked_reason"] = transfer["state"], reason
        self._move(transfer_id, TransferState.BLOCKED)

    def unblock(self, transfer_id: str) -> None:
        """Go back to the state it paused in (already scan-verified, so allowed)."""
        transfer = self.transfers[transfer_id]
        transfer["blocked_reason"] = None
        self._move(transfer_id, transfer["blocked_from"])

    def set_state(self, transfer_id: str, state: str) -> None:
        if state in PHYSICAL:
            raise StateConflict(f"{state} can only be set by a verified scan")
        self._move(transfer_id, state)

    def apply_verified_scan(
        self, transfer_id: str, expect: str, next_state: str, scan: dict
    ) -> None:
        transfer = self.transfers[transfer_id]
        if transfer["state"] != expect:
            raise StateConflict(f"{transfer_id} is {transfer['state']}, expected {expect}")
        sample = self.samples[scan["sample_id"]]
        sample["location_id"] = scan["location_id"]
        sample["custodian"] = scan.get("to_custodian")
        if scan["type"] == "checkout":
            sample["status"], sample["checked_out_at"] = "out", scan["scanned_at"]
        elif scan["type"] == "checkin":
            sample["status"], sample["checked_out_at"] = "stored", None
        self._move(transfer_id, next_state, scan=scan)

    def _move(self, transfer_id: str, state: str, scan: dict | None = None) -> None:
        transfer = self.transfers[transfer_id]
        transfer["state"] = str(state)
        transfer["updated_at"] = datetime.now(UTC)
        transfer["history"].append({"state": str(state), "at": transfer["updated_at"]})

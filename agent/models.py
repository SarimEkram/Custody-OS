"""Pydantic models for every collection (shared contract; mirror in dashboard/src/lib/types.ts).

Field names match docs/DATA_MODEL.md. Times are timezone-aware UTC datetimes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Doc(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True, extra="allow", use_enum_values=True)

    def to_mongo(self) -> dict[str, Any]:
        """Dict ready for insert: keeps `_id` alias, drops unset optional `_id`."""
        d = self.model_dump(by_alias=True, mode="python")
        if d.get("_id") is None:
            d.pop("_id", None)
        return d


# ------------------------------------------------------------------ enums
class TransferState(StrEnum):
    requested = "requested"
    reserved = "reserved"
    checked_out = "checked_out"
    in_transit = "in_transit"
    received = "received"
    blocked = "blocked"
    revoked = "revoked"
    recovering = "recovering"
    recovered = "recovered"
    cancelled = "cancelled"


TERMINAL_STATES = {TransferState.received, TransferState.recovered, TransferState.cancelled}


class Decision(StrEnum):
    allow = "allow"
    deny = "deny"
    pending_approval = "pending_approval"
    pending_confirmation = "pending_confirmation"
    halt = "halt"


class EntryType(StrEnum):
    intake = "intake"
    confirmation = "confirmation"
    decision = "decision"
    outcome = "outcome"
    verification = "verification"
    compensation = "compensation"
    recovery = "recovery"
    exposure = "exposure"
    violation = "violation"
    correction = "correction"


class OutcomeStatus(StrEnum):
    sent = "sent"
    confirmed = "confirmed"
    mismatch = "mismatch"
    unconfirmed = "unconfirmed"


class ScanType(StrEnum):
    checkout = "checkout"
    handoff = "handoff"
    intake = "intake"
    checkin = "checkin"
    seal_check = "seal_check"


Restriction = Literal["standard", "controlled", "forensic"]
RESTRICTION_RANK = {"standard": 0, "controlled": 1, "forensic": 2}


# ------------------------------------------------------------------ reference data
class Identity(_Doc):
    id: str = Field(alias="_id")  # user:jlee | agent:custody-01 | scanner:reader-fzB
    kind: Literal["human", "agent", "scanner"]
    name: str | None = None
    role: str | None = None
    clearances: list[Restriction] = []
    supervisor_id: str | None = None
    location_id: str | None = None
    model: str | None = None


class GrantScope(BaseModel):
    actions: list[str]
    sample_types: list[str] = []
    locations: list[str] = []
    max_restriction: Restriction = "standard"


class Grant(_Doc):
    id: str = Field(alias="_id")  # GRANT-0007
    grantor: str  # user:...
    grantee: str  # agent:...
    status: Literal["active", "revoked"] = "active"
    scope: GrantScope
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None


class Policy(_Doc):
    id: str = Field(alias="_id")  # custody-default@3
    policy_id: str
    version: int
    active: bool
    rules: dict[str, Any]
    created_by: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Sample(_Doc):
    id: str = Field(alias="_id")  # TS-1042
    type: str  # tissue | evidence
    description: str = ""
    restriction: Restriction = "standard"
    required_certifications: list[str] = []
    location_id: str
    custodian: str
    status: Literal["stored", "out", "compromised", "consumed"] = "stored"
    max_minutes_out: int = 20
    checked_out_at: datetime | None = None
    case_ref: str | None = None


class Location(_Doc):
    id: str = Field(alias="_id")  # FZ-B
    name: str
    kind: Literal["freezer", "station", "corridor", "lab"]
    certifications: list[str] = []
    map: dict[str, int] = {}
    travel_minutes: dict[str, int] = {}


class Incident(_Doc):
    id: str = Field(alias="_id")
    summary: str
    tags: list[str] = []
    severity: Literal["low", "medium", "high"] = "medium"


# ------------------------------------------------------------------ requests and task state
class SttInfo(BaseModel):
    provider: str = "elevenlabs"
    model_id: str
    language_code: str | None = None
    language_probability: float | None = None
    keyterms: list[str] = []
    transcription_id: str | None = None


class Word(BaseModel):
    text: str
    start: float | None = None
    end: float | None = None
    logprob: float | None = None


class VoiceRequest(_Doc):
    id: str = Field(alias="_id")  # VR-0019
    source: Literal["voice", "typed"]
    speaker_id: str  # logged-in identity, never inferred from audio
    audio_file_id: ObjectId | None = None
    audio_sha256: str | None = None
    audio_duration_secs: float | None = None
    stt: SttInfo | None = None
    transcript: str = Field(min_length=1)
    words: list[Word] = []
    low_confidence_terms: list[str] = []
    parsed: dict[str, Any] | None = None
    transfer_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _voice_needs_audio(self) -> VoiceRequest:
        if self.source == "voice" and not (self.audio_file_id and self.audio_sha256 and self.stt):
            raise ValueError("voice requests need audio_file_id, audio_sha256 and stt")
        return self


class StateChange(BaseModel):
    state: TransferState
    at: datetime = Field(default_factory=utcnow)
    scan_id: ObjectId | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True)


class Transfer(_Doc):
    id: str = Field(alias="_id")  # TR-0031
    sample_id: str
    from_: str = Field(alias="from")
    to: str
    window_start: datetime | None = None
    requested_by: str
    grant_id: str
    request_id: str | None = None
    source: Literal["voice", "typed"] = "typed"
    readback_confirmed: bool = False
    blocked_reason: Literal["approval_pending", "readback_pending"] | None = None
    blocked_from: TransferState | None = None  # state to return to when unblocked
    request_text: str = ""
    state: TransferState = TransferState.requested
    halt: bool = False
    halt_reason: str | None = None
    reservation_id: str | None = None
    courier_job_id: str | None = None
    approval_id: str | None = None
    history: list[StateChange] = []
    updated_at: datetime = Field(default_factory=utcnow)


class Approval(_Doc):
    id: str = Field(alias="_id")  # APR-0012
    transfer_id: str
    sample_id: str | None = None
    requested_for_tool: str
    reason_requested: str
    approver_role: str = "supervisor"
    status: Literal["pending", "approved", "denied"] = "pending"
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None


class Reservation(_Doc):
    id: str = Field(alias="_id")
    station_id: str
    transfer_id: str
    status: Literal["held", "cancelled", "used"] = "held"
    expires_at: datetime


class ControlCommand(_Doc):
    id: ObjectId | None = Field(default=None, alias="_id")
    type: Literal["stop", "resume"] = "stop"
    scope: Literal["all", "transfer"] = "all"
    transfer_id: str | None = None
    issued_by: str
    at: datetime = Field(default_factory=utcnow)


# ------------------------------------------------------------------ audit records
class LedgerEntry(_Doc):
    """One append-only ledger row. Only agent/ledger.py inserts these."""

    id: ObjectId | None = Field(default=None, alias="_id")
    timestamp: datetime = Field(default_factory=utcnow)
    entry_type: EntryType
    transfer_id: str
    sample_id: str | None = None
    request_id: str | None = None
    correlation_id: str
    actor: str  # agent:... | user:... | system:...
    on_behalf_of: str  # user:...
    grant_id: str | None
    grant_snapshot: dict[str, Any] | None = None
    policy_id: str
    policy_version: int
    tool: str | None = None
    args: dict[str, Any] | None = None
    reversible: bool | None = None
    reason: str | None = None
    decision: Decision
    rule_hits: list[str] = []
    risk_flags: list[str] = []
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    status: OutcomeStatus | None = None
    refs: list[ObjectId] = []
    summary: str  # one plain-English sentence; Atlas embeds it for auditor search

    @model_validator(mode="after")
    def _check_prefixes(self) -> LedgerEntry:
        if not self.actor.startswith(("agent:", "user:", "system:")):
            raise ValueError("actor must start with agent:, user: or system:")
        if not self.on_behalf_of.startswith("user:"):
            raise ValueError("on_behalf_of must be a user:")
        return self


class CustodyEvent(_Doc):
    """A scan. Only the scanner service inserts these (scanner_svc DB user)."""

    id: ObjectId | None = Field(default=None, alias="_id")
    scanner_id: str  # scanner:reader-fzB
    type: ScanType
    sample_id: str
    location_id: str
    from_custodian: str | None = None
    to_custodian: str | None = None
    correlation_id: str | None = None
    seal_id: str | None = None
    scanned_at: datetime = Field(default_factory=utcnow)
    signature: str  # hmac-sha256:<hex>

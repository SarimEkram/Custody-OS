"""Read-back confirmation for voice-originated transfers (CLAUDE.md rule 6).

The read-back text is built by code from the validated transfer, never from the model's own
summary, so what the tech hears is exactly what will run. Only the person who made the
request can confirm it. The confirmation is a ledger entry; the gate (A) refuses irreversible
tools until it exists.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from agent.parse import Catalog
from agent.signals import Signals
from agent.transfers import MemoryCustodyStore


class NoPending(Exception):
    pass


class NotRequester(Exception):
    pass


def readback_text(
    transfer: dict, catalog: Catalog, eta_minutes: float | None, now: datetime
) -> str:
    src, dst = catalog.locations[transfer["from"]], catalog.locations[transfer["to"]]
    text = f"I'll check out {transfer['sample_id']} from {src} and send it to {dst}"
    if eta_minutes is not None:
        arrival = now + timedelta(minutes=eta_minutes)
        text += f", arriving about {arrival.hour % 12 or 12}:{arrival.minute:02d}"
    return text + ". Confirm?"


class ConfirmationService:
    def __init__(
        self,
        store: MemoryCustodyStore,
        append: Callable[[dict], Awaitable[Any]],
        signals: Signals,
        emit: Callable[[dict], None],
        catalog: Catalog,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        recorder: Callable[[str, str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.store, self.append, self.signals, self.emit = store, append, signals, emit
        self.catalog, self.clock = catalog, clock
        self.recorder = recorder  # Atlas mode: the gate records the confirmation and unblocks
        self.pending: dict[str, dict] = {}
        self.count = 0

    async def request(self, transfer_id: str, eta_minutes: float | None) -> dict:
        transfer = await self.store.get_transfer(transfer_id)
        text = readback_text(transfer, self.catalog, eta_minutes, self.clock())
        self.count += 1
        conf_id = f"CONF-{self.count}"
        self.pending[transfer_id] = {"id": conf_id, "text": text}
        self.signals.clear(f"confirm:{transfer_id}")
        self.emit({"kind": "confirmation_request",
                   "data": {"id": conf_id, "transfer_id": transfer_id, "text": text}})  # fmt: skip
        return {"confirmation_request_id": conf_id, "readback_text": text}

    async def confirm(self, transfer_id: str, user_id: str, method: str) -> None:
        transfer = await self.store.get_transfer(transfer_id)  # KeyError -> unknown transfer
        pending = self.pending.get(transfer_id)
        if not pending:
            raise NoPending(transfer_id)
        if user_id != transfer["requested_by"]:
            raise NotRequester(user_id)
        if self.recorder:
            try:
                await self.recorder(transfer_id, user_id, pending["text"], method)
            except PermissionError as e:
                raise NotRequester(user_id) from e
            except ValueError as e:
                raise KeyError(transfer_id) from e
        else:
            await self.append({
                "timestamp": self.clock(), "entry_type": "confirmation", "actor": user_id,
                "on_behalf_of": user_id, "grant_id": transfer["grant_id"], "policy_id": "readback",
                "policy_version": 1, "decision": "allow", "transfer_id": transfer_id,
                "sample_id": transfer["sample_id"], "request_id": transfer["request_id"],
                "correlation_id": f"c-{uuid.uuid4().hex[:8]}", "tool": None,
                "response": {"readback_text": pending["text"], "confirmed_by": user_id,
                             "method": method, "confirmation_request_id": pending["id"]},
                "summary": f"{user_id} confirmed the read-back by {method}.",
            })  # fmt: skip
            await self.store.confirm_readback(transfer_id)
        del self.pending[transfer_id]
        self.signals.signal(f"confirm:{transfer_id}", "confirmed")


def make_request_confirmation_handler(confirmations: ConfirmationService):
    """Executor handler for the internal request_confirmation tool."""

    async def handle(args: dict, ctx) -> dict:
        out = await confirmations.request(ctx.transfer_id, ctx.refs.get("eta_minutes"))
        return {"status": "waiting_for_tech", **out}

    return handle

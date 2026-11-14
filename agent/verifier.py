"""Verifier: a step counts as done when a matching scan exists, not when the agent says so.

ScanBus receives custody_events (A's change-stream watcher registers `on_scan`). The verifier
waits for the scan with the call's correlation_id, checks type / sample / place, and only then
advances state. Outcomes: confirmed | mismatch | unconfirmed. Each is written to the ledger.

shortcut: wait_for polls a dict every 10 ms (thread-safe, trivial); switch to an asyncio.Event
per correlation_id if polling ever shows up in a profile.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from agent.executor import RunContext
from agent.tools import TOOLS
from agent.transfers import MemoryCustodyStore, StateConflict

# tool -> (state the transfer must be in, state it moves to, where the scan must happen)
NEXT = {
    "checkout_sample": ("reserved", "checked_out", "from"),
    "handoff_to_courier": ("checked_out", "in_transit", "from"),
    "deliver": ("in_transit", "received", "to"),
    "return_to_storage": ("recovering", "recovered", "return_to"),
}


class ScanBus:
    def __init__(self) -> None:
        self.scans: dict[str, dict] = {}

    def on_scan(self, scan: dict) -> None:
        self.scans[scan["correlation_id"]] = scan

    async def wait_for(self, correlation_id: str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if correlation_id in self.scans:
                return self.scans[correlation_id]
            await asyncio.sleep(0.01)
        return self.scans.get(correlation_id)


class Verifier:
    def __init__(
        self,
        bus: ScanBus,
        store: MemoryCustodyStore,
        append: Callable[[dict], Awaitable[Any]],
        timeout: float = 10.0,
    ) -> None:
        self.bus, self.store, self.append, self.timeout = bus, store, append, timeout

    def _entry(self, ctx: RunContext, cid: str, outcome_id, kind: str, status, summary: str, **x):
        return {
            "timestamp": datetime.now(UTC), "entry_type": kind, "actor": "system:verifier",
            "on_behalf_of": ctx.on_behalf_of, "grant_id": ctx.grant_id,
            "policy_id": "verification", "policy_version": 1, "decision": "allow",
            "transfer_id": ctx.transfer_id, "sample_id": ctx.refs.get("sample_id"),
            "request_id": ctx.request_id, "correlation_id": cid, "status": status,
            "refs": [outcome_id], "summary": summary, **x,
        }  # fmt: skip

    async def confirm(self, *, correlation_id: str, tool: str, ctx: RunContext, outcome_id) -> str:
        expect_state, next_state, where = NEXT[tool]
        scan = await self.bus.wait_for(correlation_id, self.timeout)
        sample = ctx.refs["sample_id"]
        if scan is None:
            await self.append(self._entry(
                ctx, correlation_id, outcome_id, "verification", "unconfirmed",
                f"No scan confirmed {tool} for {sample} before the timeout.",
            ))  # fmt: skip
            return "unconfirmed"

        problem = None
        if (scan["type"], scan["sample_id"]) != (TOOLS[tool].expected_scan, sample):
            want = f"{TOOLS[tool].expected_scan} of {sample}"
            problem = f"expected {want}, scanned {scan['type']} of {scan['sample_id']}"
        elif scan["location_id"] != ctx.refs[where]:
            problem = f"expected scan at {ctx.refs[where]}, scanned at {scan['location_id']}"
        else:
            try:
                self.store.apply_verified_scan(ctx.transfer_id, expect_state, next_state, scan)
            except StateConflict as e:
                problem = str(e)
        if problem:
            await self.append(self._entry(
                ctx, correlation_id, outcome_id, "verification", "mismatch",
                f"Scan disagrees with {tool}: {problem}.",
            ))  # fmt: skip
            await self.append(self._entry(
                ctx, correlation_id, outcome_id, "violation", None,
                f"Custody mismatch on {sample}: {problem}.", rule_hits=["scan_mismatch"],
            ))  # fmt: skip
            return "mismatch"

        await self.append(self._entry(
            ctx, correlation_id, outcome_id, "verification", "confirmed",
            f"Scanner confirmed {tool} for {sample}; transfer is now {next_state}.",
        ))  # fmt: skip
        return "confirmed"

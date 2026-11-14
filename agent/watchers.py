"""Change-stream watchers: revocation/Stop -> halt, approval -> resume, scan -> verify, plus the SSE feed.

Usage (in the agent service's startup):

    watchers = Watchers(on_halt=recovery.start, on_approval=loop.resume, on_scan=verifier.handle)
    asyncio.create_task(watchers.run())

    # SSE endpoint (C's dashboard):  async for event in event_feed(): yield f"data: {json.dumps(event)}\\n\\n"

Halting is done HERE, not in the agent loop: the watcher sets transfers.halt (so the gate's next check()
returns `halt`), moves the transfer to `revoked`, writes a `halt` ledger row immediately (the dashboard
shows it even if the agent is idle waiting for a courier), then calls on_halt so B starts recovery.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any

from bson import ObjectId

from agent import ledger
from agent.db import agent_db
from agent.models import Decision, EntryType, TransferState, utcnow

log = logging.getLogger("custody.watchers")

HaltCallback = Callable[[dict, str, str], Awaitable[None]]  # (transfer, reason, by_user)
DocCallback = Callable[[dict], Awaitable[None]]

ACTIVE = {"$nin": [TransferState.received, TransferState.recovered, TransferState.cancelled]}
EXPIRY_SWEEP_SECONDS = 15


async def _noop(*_: Any) -> None:
    return None


async def halt_transfers(query: dict, reason: str, by: str | None, rule: str) -> list[dict]:
    """Set the halt flag on every matching active transfer and write one `halt` ledger row each."""
    db = agent_db()
    policy = await db.policies.find_one({"policy_id": "custody-default", "active": True}, sort=[("version", -1)])
    halted = []
    async for tr in db.transfers.find({**query, "state": ACTIVE, "halt": {"$ne": True}}):
        now = utcnow()
        new_state = tr["state"] if tr["state"] in (TransferState.revoked, TransferState.recovering) \
            else TransferState.revoked
        res = await db.transfers.update_one(
            {"_id": tr["_id"], "halt": {"$ne": True}},
            {"$set": {"halt": True, "halt_reason": reason, "state": new_state, "updated_at": now},
             "$push": {"history": {"state": new_state, "at": now}}})
        if res.modified_count != 1:
            continue  # another watcher got there first
        who = by if by and by.startswith("user:") else tr["requested_by"]
        await ledger.append({
            "entry_type": EntryType.decision, "transfer_id": tr["_id"], "sample_id": tr["sample_id"],
            "request_id": tr.get("request_id"), "correlation_id": f"halt-{ObjectId()}", "actor": "system:watcher",
            "on_behalf_of": who, "grant_id": tr.get("grant_id"), "policy_id": policy["policy_id"],
            "policy_version": policy["version"], "decision": Decision.halt, "rule_hits": [rule], "reason": reason,
            "response": {"previous_state": tr["state"]},
            "summary": f"Agent halted on {tr['_id']} ({tr['sample_id']}, was {tr['state']}): {reason}.",
        })
        halted.append({**tr, "halt": True, "state": new_state, "halt_reason": reason, "_halted_by": who})
    return halted


class Watchers:
    def __init__(self, *, on_halt: HaltCallback = _noop, on_approval: DocCallback = _noop,
                 on_scan: DocCallback = _noop):
        self.on_halt, self.on_approval, self.on_scan = on_halt, on_approval, on_scan
        self._tokens: dict[str, Any] = {}  # resume tokens (in memory)
        self._expired_seen: set[str] = set()

    async def run(self) -> None:
        await asyncio.gather(
            self._loop("grants", self._grants_pipeline(), self._on_grant_change, full_document="updateLookup"),
            self._loop("control", [{"$match": {"operationType": "insert", "fullDocument.type": "stop"}}],
                       self._on_control),
            self._loop("approvals", [{"$match": {"operationType": "update", "updateDescription.updatedFields.status":
                                                 {"$in": ["approved", "denied"]}}}],
                       self._on_approval_change, full_document="updateLookup"),
            self._loop("custody_events", [{"$match": {"operationType": "insert"}}],
                       lambda ch: self.on_scan(ch["fullDocument"])),
            self._expiry_sweep(),
        )

    # ---------------------------------------------------------- generic resilient stream loop
    async def _loop(self, coll: str, pipeline: list, handler: Callable[[dict], Awaitable[None]], **kw) -> None:
        backoff = 1
        while True:
            try:
                async with await agent_db()[coll].watch(pipeline, resume_after=self._tokens.get(coll), **kw) as s:
                    log.info("watching %s", coll)
                    backoff = 1
                    async for ch in s:
                        self._tokens[coll] = s.resume_token
                        try:
                            await handler(ch)
                        except Exception:  # one bad event must not kill the watcher
                            log.exception("handler for %s failed", coll)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("change stream on %s dropped; retrying in %ss", coll, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    # ---------------------------------------------------------- grants: revoke / TTL delete -> halt
    @staticmethod
    def _grants_pipeline() -> list:
        return [{"$match": {"$or": [
            {"operationType": "update", "updateDescription.updatedFields.status": "revoked"},
            {"operationType": "delete"},  # TTL expiry (or manual delete)
        ]}}]

    async def _on_grant_change(self, ch: dict) -> None:
        grant_id = ch["documentKey"]["_id"]
        if ch["operationType"] == "delete":
            reason, by, rule = f"grant {grant_id} expired (TTL removed it)", None, "grant_expired"
        else:
            g = ch.get("fullDocument") or {}
            by = g.get("revoked_by") or g.get("grantor")
            reason = f"grant {grant_id} revoked by {by.split(':')[-1] if by else 'unknown'}" + \
                     (f" ({g['revoke_reason']})" if g.get("revoke_reason") else "")
            rule = "grant_revoked"
        await self._halt({"grant_id": grant_id}, reason, by, rule)

    async def _expiry_sweep(self) -> None:
        """TTL runs ~every 60s; catch expiry sooner so a lapsed grant halts within ~15s."""
        while True:
            try:
                async for g in agent_db().grants.find({"status": "active", "expires_at": {"$lte": utcnow()}}):
                    if g["_id"] not in self._expired_seen:
                        self._expired_seen.add(g["_id"])
                        await self._halt({"grant_id": g["_id"]}, f"grant {g['_id']} expired at {g['expires_at']}",
                                         g.get("grantor"), "grant_expired")
            except Exception:
                log.exception("expiry sweep failed")
            await asyncio.sleep(EXPIRY_SWEEP_SECONDS)

    # ---------------------------------------------------------- control: Stop button
    async def _on_control(self, ch: dict) -> None:
        cmd = ch["fullDocument"]
        query = {"_id": cmd["transfer_id"]} if cmd.get("scope") == "transfer" and cmd.get("transfer_id") else {}
        by = cmd.get("issued_by")
        await self._halt(query, f"Stop pressed by {by.split(':')[-1] if by else 'unknown'}", by, "stop_switch")

    async def _halt(self, query: dict, reason: str, by: str | None, rule: str) -> None:
        for tr in await halt_transfers(query, reason, by, rule):
            await self.on_halt(tr, reason, tr["_halted_by"])

    # ---------------------------------------------------------- approvals: resume / cancel
    async def _on_approval_change(self, ch: dict) -> None:
        if ch.get("fullDocument"):
            await self.on_approval(ch["fullDocument"])


# -------------------------------------------------------------- dashboard event feed (SSE)
FEED = {"ledger": "ledger", "transfers": "transfer", "custody_events": "scan", "approvals": "approval"}


def jsonable(v: Any) -> Any:
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items() if k != "embedding"}
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    return v


async def event_feed() -> AsyncIterator[dict]:
    """Merged change streams -> `{kind, data}` events (BUILD_PLAN SSE shape). One per connected dashboard."""
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)

    async def pump(coll: str, kind: str) -> None:
        async with await agent_db()[coll].watch([{"$match": {"operationType": {"$in": ["insert", "update",
                                                                                       "replace"]}}}],
                                                full_document="updateLookup") as s:
            async for ch in s:
                if ch.get("fullDocument"):
                    await queue.put({"kind": kind, "data": jsonable(ch["fullDocument"])})

    tasks = [asyncio.create_task(pump(c, k)) for c, k in FEED.items()]
    try:
        while True:
            yield await queue.get()
    finally:
        for t in tasks:
            t.cancel()

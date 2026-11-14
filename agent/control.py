"""Human controls: revoke a grant, press Stop. Called by the agent API (dashboard buttons) and scripts/.

Revocation uses admin_svc on purpose: the agent's own DB user cannot edit grants (db/roles.md).
The watchers (agent/watchers.py) see the change and halt the affected transfers.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta

from bson import ObjectId

from agent import ledger
from agent.db import admin_db, agent_db
from agent.models import utcnow

REISSUE_MINUTES = 60  # a replacement grant is short-lived; the server decides, never the client
SYSTEM_TRANSFER = "SYSTEM"  # ledger rows need a transfer_id; a grant correction belongs to none


class Conflict(Exception):
    """The request is valid but the grants are not in a state that allows it (HTTP 409)."""


async def revoke_grant(grant_id: str, by: str, reason: str = "") -> dict:
    if not by.startswith("user:"):
        raise ValueError("revoked_by must be a user: identity")
    db = admin_db()
    g = await db.grants.find_one({"_id": grant_id})
    if g is None:
        raise LookupError(f"grant {grant_id} not found")
    if g["status"] == "revoked":
        return g
    me = await db.identities.find_one({"_id": by}) or {}
    if by != g["grantor"] and me.get("role") != "supervisor":
        raise PermissionError("only the grantor or a supervisor can revoke a grant")
    now = utcnow()
    await db.grants.update_one(
        {"_id": grant_id, "status": "active"},
        {
            "$set": {
                "status": "revoked",
                "revoked_at": now,
                "revoked_by": by,
                "revoke_reason": reason or None,
            }
        },
    )
    return {
        **g,
        "status": "revoked",
        "revoked_at": now,
        "revoked_by": by,
        "revoke_reason": reason or None,
    }


async def press_stop(by: str, transfer_id: str | None = None) -> ObjectId:
    """Insert a stop command; the `control` watcher halts all (or one) active transfers."""
    res = await agent_db().control.insert_one(
        {
            "type": "stop",
            "scope": "transfer" if transfer_id else "all",
            "transfer_id": transfer_id,
            "issued_by": by,
            "at": utcnow(),
        }
    )
    return res.inserted_id


def plan_reissue(
    old: dict | None,
    by: str,
    by_identity: dict | None,
    reason: str,
    now: datetime,
    new_id: str,
    active_exists: bool,
    policy: dict,
) -> tuple[dict, dict]:
    """Pure rules for restoring authority: returns (the new grant, the ledger row), or raises.

    A NEW grant is issued; the revoked one is never reactivated. Only a human supervisor may do it
    (never the agent, never the grantor alone). The client chooses nothing but the reason: the scope
    is copied from the old grant, so it can never be widened, and the expiry is set here.
    Refusals about who you are come first, so a non-supervisor learns nothing about the grants.
    """
    if by.startswith("agent:"):
        raise PermissionError("The agent cannot grant itself authority.")
    if (
        not by_identity
        or by_identity.get("role") != "supervisor"
        or by_identity.get("kind") != "human"
    ):
        raise PermissionError("Only a supervisor can issue a new grant.")
    reason = (reason or "").strip()
    if len(reason) < 8:
        raise ValueError("A reason of at least 8 characters is required.")
    if old is None:
        raise LookupError("unknown grant")
    if old.get("status") == "active":
        raise Conflict(f"{old['_id']} is still active, so there is nothing to replace.")
    if old.get("superseded_by"):
        raise Conflict(f"{old['_id']} was already replaced by {old['superseded_by']}.")
    if active_exists:
        raise Conflict("An active grant already exists for this agent.")

    expires = now + timedelta(minutes=REISSUE_MINUTES)
    new = {
        "_id": new_id,
        "grantor": old["grantor"],
        "grantee": old["grantee"],
        "status": "active",
        "scope": json.loads(json.dumps(old["scope"])),
        "created_at": now,
        "expires_at": expires,
        "supersedes": old["_id"],
        "issued_by": by,
    }
    row = {
        "timestamp": now,
        "entry_type": "correction",
        "transfer_id": SYSTEM_TRANSFER,
        "correlation_id": f"c-{uuid.uuid4().hex[:8]}",
        "request_id": None,
        "actor": by,
        "on_behalf_of": old["grantor"],
        "grant_id": new_id,
        "policy_id": policy["policy_id"],
        "policy_version": policy["version"],
        "decision": "allow",
        "rule_hits": ["grant_reissued"],
        "reason": reason,
        "response": {"supersedes": old["_id"], "expires_at": expires},
        "summary": f"{by.split(':', 1)[-1]} issued {new_id} to replace revoked {old['_id']}. "
        f"Same scope, expires in {REISSUE_MINUTES} min. Reason: {reason}",
    }
    return new, row


def _next_grant_id(ids: list[str]) -> str:
    nums = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"GRANT-(\d+)", str(i)))]
    return f"GRANT-{max(nums, default=0) + 1:04d}"


async def reissue_grant(grant_id: str, by: str, reason: str = "") -> dict:
    """Issue a replacement for a revoked grant. Written with admin_svc (the agent's DB user cannot
    touch grants) in ONE transaction: new grant, `superseded_by` on the old one, and the ledger row.
    The guarded update means a second reissue of the same grant cannot succeed."""
    db = admin_db()
    async with db.client.start_session() as s:
        async with await s.start_transaction():
            old = await db.grants.find_one({"_id": grant_id}, session=s)
            who = await db.identities.find_one({"_id": by}, session=s)
            now = utcnow()
            active = (
                bool(old)
                and await db.grants.find_one(
                    {
                        "grantor": old["grantor"],
                        "grantee": old["grantee"],
                        "status": "active",
                        "expires_at": {"$gt": now},
                    },
                    session=s,
                )
                is not None
            )
            policy = await db.policies.find_one(
                {"policy_id": "custody-default", "active": True}, sort=[("version", -1)], session=s
            )
            if policy is None:
                raise RuntimeError("no active policy 'custody-default'; run db.seed")
            ids = [d["_id"] async for d in db.grants.find({}, {"_id": 1}, session=s)]
            try:
                new, row = plan_reissue(
                    old, by, who, reason, now, _next_grant_id(ids), active, policy
                )
            except LookupError:
                raise LookupError(f"unknown grant {grant_id}") from None
            marked = await db.grants.update_one(
                {"_id": grant_id, "status": "revoked", "superseded_by": {"$exists": False}},
                {"$set": {"superseded_by": new["_id"]}},
                session=s,
            )
            if marked.modified_count != 1:
                raise Conflict(f"{grant_id} was already replaced.")
            await db.grants.insert_one(new, session=s)
            await ledger.append(row, session=s, db=db)
    return {"grant_id": new["_id"], "supersedes": grant_id, "expires_at": new["expires_at"]}

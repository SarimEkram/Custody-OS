"""Human controls: revoke a grant, press Stop. Called by the agent API (dashboard buttons) and scripts/.

Revocation uses admin_svc on purpose: the agent's own DB user cannot edit grants (db/roles.md).
The watchers (agent/watchers.py) see the change and halt the affected transfers.
"""

from __future__ import annotations

from bson import ObjectId

from agent.db import admin_db, agent_db
from agent.models import utcnow


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
    await db.grants.update_one({"_id": grant_id, "status": "active"},
                               {"$set": {"status": "revoked", "revoked_at": now, "revoked_by": by,
                                         "revoke_reason": reason or None}})
    return {**g, "status": "revoked", "revoked_at": now, "revoked_by": by, "revoke_reason": reason or None}


async def press_stop(by: str, transfer_id: str | None = None) -> ObjectId:
    """Insert a stop command; the `control` watcher halts all (or one) active transfers."""
    res = await agent_db().control.insert_one({
        "type": "stop", "scope": "transfer" if transfer_id else "all", "transfer_id": transfer_id,
        "issued_by": by, "at": utcnow()})
    return res.inserted_id

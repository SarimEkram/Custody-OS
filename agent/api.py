"""FastAPI routes for Person A's pieces. B mounts this in the agent service:

    from agent.api import router as custody_router
    app.include_router(custody_router)

Routes (dashboard calls them through the Vite proxy as /api/...):
    POST /transfers/{id}/confirm      read-back confirmed by the tech
    POST /approvals/{id}              supervisor approve/deny
    POST /grants/{id}/revoke          revoke a grant (uses admin_svc)
    POST /control/stop                Stop switch (all, or one transfer)
    GET  /stream                      SSE: {kind, data} for ledger/transfer/scan/approval changes
    POST /audit/query                 plain-English auditor question
    GET  /audit/chain/{sample_id}     custody chain timeline
    GET  /audit/transfer/{id}         one transfer back to its spoken request
    GET  /audit/gaps | /audit/restricted-without-supervisor | /audit/exposure | /audit/denials
    GET  /audit/requests/{id}/verify  re-hash the stored audio
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import audit
from agent.control import press_stop, revoke_grant
from agent.policy_gate import confirm_readback, decide_approval
from agent.watchers import event_feed, jsonable

router = APIRouter()


class ConfirmBody(BaseModel):
    user_id: str
    readback_text: str
    method: Literal["button", "voice"] = "button"


class ApprovalBody(BaseModel):
    decided_by: str
    approve: bool
    note: str = ""


class RevokeBody(BaseModel):
    by: str
    reason: str = ""


class StopBody(BaseModel):
    by: str
    transfer_id: str | None = None


class AuditQuery(BaseModel):
    question: str


def _http(e: Exception) -> HTTPException:
    code = 403 if isinstance(e, PermissionError) else 404 if isinstance(e, LookupError) else 400
    return HTTPException(code, str(e))


@router.post("/transfers/{transfer_id}/confirm")
async def confirm(transfer_id: str, body: ConfirmBody):
    try:
        lid = await confirm_readback(transfer_id, body.user_id, body.readback_text, body.method)
    except (ValueError, PermissionError) as e:
        raise _http(e) from e
    return {"ok": True, "ledger_id": str(lid)}


@router.post("/approvals/{approval_id}")
async def approval(approval_id: str, body: ApprovalBody):
    try:
        return jsonable(await decide_approval(approval_id, body.decided_by, body.approve, body.note))
    except (ValueError, PermissionError) as e:
        raise _http(e) from e


@router.post("/grants/{grant_id}/revoke")
async def revoke(grant_id: str, body: RevokeBody):
    try:
        return jsonable(await revoke_grant(grant_id, body.by, body.reason))
    except (ValueError, PermissionError, LookupError) as e:
        raise _http(e) from e


@router.post("/control/stop")
async def stop(body: StopBody):
    return {"ok": True, "control_id": str(await press_stop(body.by, body.transfer_id))}


@router.get("/stream")
async def stream():
    async def gen():
        yield ": connected\n\n"
        async for ev in event_feed():
            yield f"data: {json.dumps(ev)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/audit/query")
async def audit_query(body: AuditQuery):
    return await audit.ask(body.question)


@router.get("/audit/chain/{sample_id}")
async def audit_chain(sample_id: str):
    return await audit.custody_chain(sample_id.upper())


@router.get("/audit/transfer/{transfer_id}")
async def audit_transfer(transfer_id: str):
    return await audit.transfer_trace(transfer_id)


@router.get("/audit/gaps")
async def audit_gaps():
    return await audit.gaps()


@router.get("/audit/restricted-without-supervisor")
async def audit_restricted():
    return await audit.restricted_without_supervisor()


@router.get("/audit/exposure")
async def audit_exposure():
    return await audit.exposure_report()


@router.get("/audit/denials")
async def audit_denials():
    return await audit.denials()


@router.get("/audit/requests/{request_id}/verify")
async def audit_verify(request_id: str):
    return await audit.verify_request_audio(request_id)

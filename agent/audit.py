"""Auditor queries: exact aggregations (preferred) + natural-language search over the ledger.

Canned pipelines come straight from docs/DATA_MODEL.md "Aggregations". `ask()` routes a plain-English
question to a canned pipeline when one fits (exact and more convincing) or to $vectorSearch over
`ledger_vector`. The LLM only fills a fixed JSON schema; it never writes pipelines.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from agent.config import settings
from agent.db import agent_db
from agent.embeddings import vector_stage
from agent.watchers import jsonable

IRREVERSIBLE = ["checkout_sample", "handoff_to_courier", "deliver"]
_HIDE = {"$project": {"embedding": 0}}


async def _agg(coll: str, pipeline: list) -> list[dict]:
    return jsonable(await (await agent_db()[coll].aggregate(pipeline)).to_list())


# ------------------------------------------------------------------ canned aggregations
async def custody_chain(sample_id: str) -> list[dict]:
    """Every ledger entry and scan for a sample, in time order (auditor timeline)."""
    return await _agg("ledger", [
        {"$match": {"sample_id": sample_id}},
        {"$unionWith": {"coll": "custody_events", "pipeline": [
            {"$match": {"sample_id": sample_id}},
            {"$addFields": {"timestamp": "$scanned_at", "entry_type": "scan",
                            "summary": {"$concat": ["Scan: ", "$type", " at ", "$location_id", " by ",
                                                    "$scanner_id"]}}}]}},
        {"$sort": {"timestamp": 1}},
        _HIDE,
    ])


async def transfer_trace(transfer_id: str) -> list[dict]:
    """From any action back to the original spoken/typed request."""
    return await _agg("ledger", [
        {"$match": {"transfer_id": transfer_id}},
        {"$sort": {"timestamp": 1}},
        {"$lookup": {"from": "voice_requests", "localField": "request_id", "foreignField": "_id",
                     "pipeline": [{"$project": {"words": 0}}], "as": "request"}},
        {"$set": {"request": {"$first": "$request"}}},
        _HIDE,
    ])


async def gaps() -> list[dict]:
    """Irreversible actions the agent says it sent, with no scanner confirmation."""
    return await _agg("ledger", [
        {"$match": {"entry_type": "outcome", "status": "sent", "reversible": False}},
        {"$lookup": {"from": "custody_events", "localField": "correlation_id", "foreignField": "correlation_id",
                     "as": "scans"}},
        {"$match": {"scans": {"$size": 0}}},
        {"$project": {"transfer_id": 1, "sample_id": 1, "tool": 1, "timestamp": 1, "on_behalf_of": 1,
                      "correlation_id": 1, "summary": 1}},
        {"$sort": {"timestamp": -1}},
    ])


async def restricted_without_supervisor() -> dict[str, list[dict]]:
    """Forensic moves allowed without an approved supervisor sign-off (should be empty), plus blocked attempts."""
    allowed = await _agg("ledger", [
        {"$match": {"entry_type": "decision", "decision": "allow", "actor": {"$regex": "^agent:"},
                    "tool": {"$in": IRREVERSIBLE}}},
        {"$lookup": {"from": "samples", "localField": "sample_id", "foreignField": "_id", "as": "s"}},
        {"$match": {"s.restriction": "forensic"}},
        {"$lookup": {"from": "approvals", "let": {"t": "$transfer_id"}, "pipeline": [
            {"$match": {"$expr": {"$and": [{"$eq": ["$transfer_id", "$$t"]}, {"$eq": ["$status", "approved"]}]}}}],
            "as": "appr"}},
        {"$match": {"appr": {"$size": 0}}},
        {"$project": {"embedding": 0, "s": 0, "appr": 0}},
    ])
    blocked = await _agg("ledger", [
        {"$match": {"entry_type": "decision", "decision": {"$in": ["deny", "pending_approval"]}}},
        {"$lookup": {"from": "samples", "localField": "sample_id", "foreignField": "_id", "as": "s"}},
        {"$match": {"s.restriction": "forensic"}},
        {"$project": {"embedding": 0, "s": 0}},
        {"$sort": {"timestamp": -1}},
    ])
    return {"violations": allowed, "blocked_attempts": blocked}


async def exposure_report() -> list[dict]:
    return await _agg("ledger", [
        {"$match": {"entry_type": {"$in": ["exposure", "violation"]}, "response.minutes_out": {"$exists": True}}},
        {"$group": {"_id": "$sample_id", "total_minutes_out": {"$sum": "$response.minutes_out"},
                    "transfers": {"$addToSet": "$transfer_id"},
                    "excursions": {"$sum": {"$cond": ["$response.exceeded", 1, 0]}}}},
        {"$sort": {"total_minutes_out": -1}},
    ])


async def denials(limit: int = 50) -> list[dict]:
    return await _agg("ledger", [
        {"$match": {"entry_type": "decision", "decision": {"$in": ["deny", "pending_approval", "halt"]}}},
        {"$sort": {"timestamp": -1}}, {"$limit": limit}, _HIDE,
    ])


async def semantic_search(text: str, filters: dict | None = None, limit: int = 15) -> list[dict]:
    stage = await vector_stage("ledger_vector", text, limit=limit, num_candidates=200, filters=filters or None)
    return await _agg("ledger", [stage, {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}}])


async def verify_request_audio(request_id: str) -> dict:
    """Re-hash the stored GridFS audio and compare with voice_requests.audio_sha256."""
    from gridfs import AsyncGridFSBucket
    db = agent_db()
    vr = await db.voice_requests.find_one({"_id": request_id})
    if not vr or not vr.get("audio_file_id"):
        return {"request_id": request_id, "has_audio": False}
    stream = await AsyncGridFSBucket(db, bucket_name="voice_audio").open_download_stream(vr["audio_file_id"])
    digest = hashlib.sha256(await stream.read()).hexdigest()
    return {"request_id": request_id, "has_audio": True, "sha256": digest, "matches": digest == vr["audio_sha256"]}


# ------------------------------------------------------------------ natural-language routing
CANNED = ["custody_chain", "transfer_trace", "gaps", "restricted_without_supervisor", "exposure_report",
          "denials", "semantic"]
_FILTER_KEYS = {"decision", "entry_type", "sample_id", "tool", "transfer_id"}

AUDIT_TOOL = {"type": "function", "function": {
    "name": "audit_query",
    "description": "Route an auditor's question about the custody ledger to one query.",
    "parameters": {"type": "object", "required": ["route"], "properties": {
        "route": {"type": "string", "enum": CANNED, "description":
                  "custody_chain: full history of one sample. transfer_trace: one transfer back to its spoken "
                  "request. gaps: actions sent but never confirmed by a scan. restricted_without_supervisor: "
                  "forensic/restricted moves without supervisor approval. exposure_report: time samples spent "
                  "out of storage. denials: blocked/denied/halted actions. semantic: anything else."},
        "sample_id": {"type": "string"}, "transfer_id": {"type": "string"},
        "semantic_query": {"type": "string", "description": "short search text for route=semantic"},
        "filters": {"type": "object", "properties": {
            "decision": {"type": "string", "enum": ["allow", "deny", "pending_approval", "pending_confirmation",
                                                    "halt"]},
            "entry_type": {"type": "string"}, "tool": {"type": "string"}}},
    }}}}

_ID = re.compile(r"\b(TS|EV)-\d{3,5}\b", re.I)
_TR = re.compile(r"\bTR-[A-Z0-9-]+\b", re.I)


def _heuristic_route(q: str) -> dict:
    """Keyword fallback when no LLM key is configured (or the LLM call fails)."""
    ql = q.lower()
    sid, tid = _ID.search(q), _TR.search(q)
    if ("restricted" in ql or "forensic" in ql) and ("supervisor" in ql or "approv" in ql):
        return {"route": "restricted_without_supervisor"}
    if any(w in ql for w in ("gap", "never confirmed", "unconfirmed", "without a scan", "no scan")):
        return {"route": "gaps"}
    if any(w in ql for w in ("exposure", "minutes out", "out of the freezer", "excursion")):
        return {"route": "exposure_report"}
    if tid:
        return {"route": "transfer_trace", "transfer_id": tid.group(0).upper()}
    if sid and any(w in ql for w in ("what happened", "history", "chain", "timeline", "trace")):
        return {"route": "custody_chain", "sample_id": sid.group(0).upper()}
    if any(w in ql for w in ("denied", "blocked", "refused", "halt", "stopped")):
        return {"route": "denials"}
    return {"route": "semantic", "semantic_query": q,
            **({"filters": {"sample_id": sid.group(0).upper()}} if sid else {})}


async def _llm_route(q: str) -> dict | None:
    if not settings.groq_api_key:
        return None
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.groq_api_key, timeout=15)
    try:
        r = await client.chat.completions.create(
            model=settings.llm_model, temperature=0, tools=[AUDIT_TOOL],
            tool_choice={"type": "function", "function": {"name": "audit_query"}},
            messages=[{"role": "system", "content": "You route lab chain-of-custody audit questions. Sample IDs "
                       "look like TS-1042 or EV-2201; transfers look like TR-0031."},
                      {"role": "user", "content": q}])
        return json.loads(r.choices[0].message.tool_calls[0].function.arguments)
    except Exception:
        return None


async def ask(question: str) -> dict[str, Any]:
    """Answer an auditor question. Returns {route, params, rows, routed_by}."""
    plan = await _llm_route(question)
    routed_by = "llm"
    if not plan or plan.get("route") not in CANNED:
        plan, routed_by = _heuristic_route(question), "keywords"
    route = plan["route"]
    if route == "custody_chain" and plan.get("sample_id"):
        rows: Any = await custody_chain(plan["sample_id"].upper())
    elif route == "transfer_trace" and plan.get("transfer_id"):
        rows = await transfer_trace(plan["transfer_id"].upper())
    elif route == "gaps":
        rows = await gaps()
    elif route == "restricted_without_supervisor":
        rows = await restricted_without_supervisor()
    elif route == "exposure_report":
        rows = await exposure_report()
    elif route == "denials":
        rows = await denials()
    else:
        route = "semantic"
        filters = {k: v for k, v in (plan.get("filters") or {}).items() if k in _FILTER_KEYS and v}
        if plan.get("sample_id"):
            filters["sample_id"] = plan["sample_id"].upper()
        plan["filters"] = filters
        rows = await semantic_search(plan.get("semantic_query") or question, filters)
    return {"question": question, "route": route, "params": plan, "routed_by": routed_by, "rows": rows}

"""The agent service: composition only. No rule or policy lives here.

    uvicorn --factory agent.main:create_app --port $AGENT_PORT

build_service() takes every backend by injection, so the wiring is testable without Atlas;
create_app() fills them from the environment and Person A's modules (gate, ledger, custody,
watchers, API router). The routes B owns are registered before A's router, so B's `/stream`
(Atlas change streams + confirmation and courier events) and `/transfers/{id}/confirm` win.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.responses import StreamingResponse

from agent.atlas import AtlasExecutor, ScanApplier
from agent.confirmation import ConfirmationService, make_request_confirmation_handler
from agent.events import CourierTicker, EventHub, merged_stream
from agent.intake import Intake, llm_parser
from agent.intake_api import create_app as create_b_routes
from agent.lab import LabClient
from agent.loop import AgentLoop
from agent.parse import Catalog, make_parse_handler
from agent.recovery import RecoveryPlanner
from agent.runner import Runner
from agent.scripted import ScriptedLLM, rule_parser
from agent.signals import Signals
from agent.voice import SpokenPrompts, VoiceIntake


@dataclass
class Backends:
    store: Any
    requests: Any
    audio: Any
    catalog: Catalog
    find_grant: Callable[[str], Awaitable[str | None]]
    append: Callable[..., Awaitable[Any]]
    execute: Callable[..., Awaitable[Any]]  # policy_gate.execute
    lab: LabClient
    llm: Any
    parser: Callable[[str], Awaitable[dict]]
    stt: Any
    router: APIRouter  # Person A's routes
    event_feed: Callable[[], AsyncIterator[dict]]
    apply_scan: Callable[[dict], Awaitable[Any]]  # custody.apply_verified_scan
    confirm_recorder: Callable[
        [str, str, str, str], Awaitable[None]
    ]  # policy_gate.confirm_readback
    record_unverified: Callable[..., Awaitable[Any]]
    read_outcomes: Callable[[str], Awaitable[list[dict]]]
    job_state: Callable[[str], Awaitable[dict]]
    list_jobs: Callable[[], Awaitable[list[dict]]]
    freezer_certs: dict[str, set[str]]
    next_state: dict
    verify_timeout: float = 10.0
    wait_timeout: float = 300.0
    low_logprob: float = -1.0
    max_audio_bytes: int = 1_048_576
    tts: Any = None
    get_approval: Callable[[str], Awaitable[dict | None]] | None = None


@dataclass
class Service:
    app: FastAPI
    hub: EventHub
    signals: Signals
    runner: Runner
    intake: Intake
    confirmations: ConfirmationService
    loop: AgentLoop
    planner: RecoveryPlanner
    internal: dict
    applier: ScanApplier
    ticker: CourierTicker
    notifications: list = field(default_factory=list)
    on_halt: Callable = None
    on_approval: Callable = None
    on_scan: Callable = None
    stream_events: Callable = None


def build_service(b: Backends) -> Service:
    hub, signals, notifications = EventHub(), Signals(), []
    applier = ScanApplier(b.apply_scan)
    speaks = bool(b.tts and b.get_approval)
    confirmations = ConfirmationService(
        b.store, b.append, signals, hub.publish, b.catalog, recorder=b.confirm_recorder,
        # the dashboard reaches the agent through its /api proxy
        audio_url=(lambda tid: f"/api/transfers/{tid}/readback/audio") if speaks else None,
    )  # fmt: skip

    async def notify(args: dict, ctx) -> dict:
        notifications.append((args["user_id"], args["message"]))
        return {"sent_to": args["user_id"]}

    internal = {
        "parse_request": make_parse_handler(b.catalog, None),
        "request_confirmation": make_request_confirmation_handler(confirmations),
        "notify": notify,
    }
    executor = AtlasExecutor(
        b.lab, internal, applier.bus, execute=b.execute, get_transfer=b.store.get_transfer,
        record_unverified=b.record_unverified, next_state=b.next_state, timeout=b.verify_timeout,
    )  # fmt: skip
    loop = AgentLoop(
        b.llm, executor, b.store, signals=signals, confirmations=confirmations,
        wait_timeout=b.wait_timeout, manage_blocking=False,  # A's gate blocks and unblocks
    )  # fmt: skip
    planner = RecoveryPlanner(
        executor, b.store, b.append, b.read_outcomes, b.job_state, b.freezer_certs,
        applier.bus, log_exposure=False,  # custody.apply_verified_scan writes exposure rows
    )  # fmt: skip
    runner = Runner(loop, planner, b.store, b.requests)
    intake = Intake(b.parser, b.catalog, b.find_grant, b.requests, b.store, b.append,
                    runner.start, b.low_logprob)  # fmt: skip
    voice = VoiceIntake(b.stt, b.audio, intake, b.catalog, b.max_audio_bytes) if b.stt else None

    speech = SpokenPrompts(b.tts, confirmations, b.get_approval) if speaks else None
    app = create_b_routes(intake, voice, confirmations, speech)

    def stream_events() -> AsyncIterator[dict]:
        return merged_stream(b.event_feed(), hub)

    @app.get("/stream")
    async def stream():
        async def gen():
            yield ": connected\n\n"
            async for event in stream_events():
                yield f"data: {json.dumps(event, default=str)}\n\n"

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )  # fmt: skip

    app.include_router(b.router)  # A's: approvals, revoke, stop, audit (B's routes above win)

    async def on_halt(transfer: dict, reason: str, by_user: str) -> None:
        await runner.halt(transfer["_id"], by_user)

    async def on_approval(doc: dict) -> None:
        signals.signal(f"approval:{doc['_id']}", doc["status"])

    return Service(
        app=app, hub=hub, signals=signals, runner=runner, intake=intake,
        confirmations=confirmations, loop=loop, planner=planner, internal=internal,
        applier=applier, ticker=CourierTicker(b.list_jobs, hub), notifications=notifications,
        on_halt=on_halt, on_approval=on_approval, on_scan=applier.handle,
        stream_events=stream_events,
    )  # fmt: skip


def create_app() -> FastAPI:
    """The real service: Atlas as agent_svc, A's gate/ledger/custody/watchers, the lab over HTTP."""
    from agent import api as atlas_api
    from agent import custody, ledger, policy_gate, watchers
    from agent.config import AGENT_ID, settings
    from agent.db import agent_client, agent_db
    from agent.llm import ChatLLM
    from agent.mongo_stores import (
        MongoAudio,
        MongoCustodyStore,
        MongoRequests,
        find_grant,
        load_catalog,
        load_freezers,
    )
    from agent.voice import ElevenLabsSTT, ElevenLabsTTS

    db, env = agent_db(), os.environ
    catalog, freezers = Catalog({}, {}), {}
    store = MongoCustodyStore(agent_client(), db)
    lab_clients = {n: httpx.AsyncClient(base_url=env[f"{n.upper()}_URL"], timeout=15.0)
                   for n in ("freezer", "scheduler", "courier", "station")}  # fmt: skip
    lab = LabClient(**lab_clients, poll=float(env.get("COURIER_POLL_SECONDS", "0.5")))

    if env.get("DEMO_SCRIPTED") == "1":
        llm, parser = (
            ScriptedLLM.load(env.get("DEMO_SCRIPT", "demo/script.json")),
            rule_parser(catalog),
        )
    else:
        llm = ChatLLM(settings.llm_base_url, settings.groq_api_key, settings.llm_model)
        parser = llm_parser(llm, catalog)

    async def read_outcomes(transfer_id: str) -> list[dict]:
        cursor = db.ledger.find({"transfer_id": transfer_id, "entry_type": "outcome"})
        return await cursor.sort("timestamp", 1).to_list()

    async def job_state(job_id: str) -> dict:
        return (await lab_clients["courier"].get(f"/jobs/{job_id}")).json()

    async def list_jobs() -> list[dict]:
        return (await lab_clients["courier"].get("/jobs")).json()

    stt = None
    if env.get("ELEVENLABS_API_KEY"):
        stt = ElevenLabsSTT(env["ELEVENLABS_API_KEY"], env.get("ELEVENLABS_STT_MODEL", "scribe_v3"),
                            timeout=float(env.get("STT_TIMEOUT_SECONDS", "5")))  # fmt: skip

    tts = None
    if env.get("ELEVENLABS_API_KEY") and env.get("ELEVENLABS_TTS_VOICE_ID"):
        tts = ElevenLabsTTS(env["ELEVENLABS_API_KEY"], env["ELEVENLABS_TTS_VOICE_ID"],
                            env.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
                            timeout=float(env.get("TTS_TIMEOUT_SECONDS", "5")))  # fmt: skip

    async def get_approval(approval_id: str) -> dict | None:
        return await db.approvals.find_one({"_id": approval_id})

    service = build_service(Backends(
        store=store, requests=MongoRequests(db), audio=MongoAudio(db), catalog=catalog,
        find_grant=find_grant(db, AGENT_ID), append=ledger.append, execute=policy_gate.execute,
        lab=lab, llm=llm, parser=parser, stt=stt, router=atlas_api.router,
        event_feed=watchers.event_feed, apply_scan=custody.apply_verified_scan,
        confirm_recorder=policy_gate.confirm_readback,
        record_unverified=custody.record_unverified, read_outcomes=read_outcomes,
        job_state=job_state, list_jobs=list_jobs, freezer_certs=freezers,
        next_state=custody.NEXT_STATE, verify_timeout=settings.verifier_timeout_seconds,
        low_logprob=float(env.get("LOW_CONFIDENCE_LOGPROB", "-1.0")),
        max_audio_bytes=int(env.get("MAX_AUDIO_BYTES", "1048576")),
        tts=tts, get_approval=get_approval,
    ))  # fmt: skip

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loaded = await load_catalog(db)
        catalog.samples.update(loaded.samples)
        catalog.locations.update(loaded.locations)
        freezers.update(await load_freezers(db))
        background = [
            asyncio.create_task(watchers.Watchers(
                on_halt=service.on_halt, on_approval=service.on_approval, on_scan=service.on_scan
            ).run()),
            asyncio.create_task(service.ticker.run()),
        ]  # fmt: skip
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            for client in lab_clients.values():
                await client.aclose()

    service.app.router.lifespan_context = lifespan
    return service.app

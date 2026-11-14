"""Request intake, shared by typed and voice requests.

A request is recorded (voice_requests), validated against the catalog, and only then does a
transfer exist. Every transfer starts with an `intake` ledger entry tying it to the original
request and to the logged-in speaker (identity comes from the login, never from the audio).

shortcut: the requested window is kept as the text the tech said ("2 PM"); converting it to
a datetime (`window_start`) belongs with the first feature that needs to schedule by time.
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from agent.llm import LLMError
from agent.parse import Catalog, validate_parse
from agent.tool_catalogue import openai_tools
from agent.transfers import MemoryCustodyStore, next_id


class NoGrant(Exception):
    """The speaker has no active grant delegating to the agent."""


class ParseFailed(Exception):
    """The model did not produce a usable parse_request call."""


class MemoryRequests:
    """In-memory voice_requests. The Mongo version is insert-only, like the ledger."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def next_id(self) -> str:
        return next_id("VR", self.docs)

    async def insert(self, doc: dict) -> None:
        self.docs[doc["_id"]] = doc

    async def get(self, request_id: str) -> dict:
        return self.docs[request_id]


Parser = Callable[[str], Awaitable[dict]]


def llm_parser(llm, catalog: Catalog) -> Parser:
    """One model call that must answer with a parse_request tool call."""
    tool = [t for t in openai_tools() if t["function"]["name"] == "parse_request"]
    # The prompt says "leave a field out if it was not said", so the schema must allow that. With every
    # field required, Groq rejected the model's call (HTTP 400) whenever the tech left out the time or
    # the source, which surfaced as a 500. The code validates what is missing (validate_parse).
    tool[0]["function"]["parameters"]["required"] = []
    places = "; ".join(f"{i} = {name}" for i, name in catalog.locations.items())
    system = (
        "Turn the lab tech's request into a parse_request call. Use location ids, not names. "
        f"Locations: {places}. Never guess: leave a field out if it was not said."
    )

    async def parse(text: str) -> dict:
        try:
            msg = await llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": text}], tool
            )
        except LLMError as e:
            if "validation failed" in str(e):  # the model's call broke the schema: ask the tech again
                raise ParseFailed("the model's parse_request call was rejected") from e
            raise
        calls = msg.get("tool_calls")
        if not calls:
            raise ParseFailed("model made no parse_request call")
        try:
            args = json.loads(calls[0]["function"]["arguments"])
        except ValueError as e:
            raise ParseFailed("unreadable parse_request arguments") from e
        args = {k: v for k, v in args.items() if k != "reason"}
        # "Move EV-2201 to the Forensics Lab": the tech did not say where it is, but the database knows.
        # This is a stored fact, not a guess, and the read-back shows the plan before anything irreversible.
        if not args.get("from") and args.get("sample_id") in catalog.samples:
            args["from"] = catalog.samples[args["sample_id"]]
        return args

    return parse


class Intake:
    def __init__(
        self,
        parser: Parser,
        catalog: Catalog,
        find_grant: Callable[[str], Awaitable[str | None]],
        requests: MemoryRequests,
        store: MemoryCustodyStore,
        append: Callable[[dict], Awaitable[Any]],
        start_run: Callable[[str], None],
        threshold: float = -1.0,
    ) -> None:
        self.parser, self.catalog, self.find_grant = parser, catalog, find_grant
        self.requests, self.store, self.append = requests, store, append
        self.start_run, self.threshold = start_run, threshold

    async def submit(
        self,
        user_id: str,
        text: str,
        source: str = "typed",
        words: list[dict] | None = None,
        audio: dict | None = None,
        stt: dict | None = None,
    ) -> dict:
        grant_id = await self.find_grant(user_id)
        if not grant_id:
            raise NoGrant(user_id)
        try:
            proposed = await self.parser(text)
        except ParseFailed:
            proposed = {}
        result = validate_parse(proposed, self.catalog, words, self.threshold)
        if not proposed:
            result.problems = [{"field": "request", "value": None, "reason": "not_understood"}]

        now = datetime.now(UTC)
        rid = await self.requests.next_id()
        transfer_id = await self.store.next_transfer_id() if result.ok else None
        audio = audio or {}
        await self.requests.insert({
            "_id": rid, "source": source, "speaker_id": user_id,
            "audio_file_id": audio.get("file_id"), "audio_sha256": audio.get("sha256"),
            "audio_duration_secs": audio.get("duration"), "stt": stt, "transcript": text,
            "words": [w for w in words or [] if w.get("type", "word") == "word"],
            "low_confidence_terms": result.low_confidence_terms,
            "parsed": result.parsed or None, "transfer_id": transfer_id, "created_at": now,
        })  # fmt: skip
        if transfer_id:
            p = result.parsed
            await self.store.create_transfer({
                "_id": transfer_id, "sample_id": p["sample_id"], "from": p["from"], "to": p["to"],
                "window": p.get("window"), "requested_by": user_id, "grant_id": grant_id,
                "request_id": rid, "source": source, "readback_confirmed": False,
                "blocked_reason": None, "request_text": text, "state": "requested",
                "halt": False, "history": [], "updated_at": now,
            })  # fmt: skip
            await self.append({
                "timestamp": now, "entry_type": "intake", "actor": user_id,
                "on_behalf_of": user_id, "grant_id": grant_id, "policy_id": "intake",
                "policy_version": 1, "decision": "allow", "transfer_id": transfer_id,
                "sample_id": p["sample_id"], "request_id": rid,
                "correlation_id": f"c-{uuid.uuid4().hex[:8]}", "tool": None,
                "response": {"transcript": text, "audio_sha256": audio.get("sha256"),
                             "model_id": (stt or {}).get("model_id")},
                "summary": f"{user_id} requested by {source}: '{text}'",
            })  # fmt: skip
            self.start_run(transfer_id)
        return {
            "request_id": rid, "transcript": text, "parsed": result.parsed,
            "low_confidence_terms": result.low_confidence_terms,
            "transfer_id": transfer_id, "problems": result.problems,
        }  # fmt: skip

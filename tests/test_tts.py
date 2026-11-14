import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.confirmation import ConfirmationService, NoPending
from agent.intake_api import create_app
from agent.parse import Catalog
from agent.signals import Signals
from agent.transfers import MemoryCustodyStore
from agent.voice import ElevenLabsTTS, SpokenPrompts, TTSError

CATALOG = Catalog({"TS-1042": "FZ-B"}, {"FZ-B": "Freezer B", "SEQ-3": "Sequencing Station 3"})
NOW = datetime(2026, 11, 14, 19, 0, tzinfo=UTC)
TRANSFER = {"_id": "TR-1", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3",
            "requested_by": "user:jlee", "grant_id": "GRANT-0007", "request_id": "VR-1",
            "state": "reserved", "readback_confirmed": False, "history": []}  # fmt: skip
APPROVAL = {"_id": "APR-1", "transfer_id": "TR-1", "sample_id": "TS-1042", "status": "pending",
            "requested_for_tool": "checkout_sample"}  # fmt: skip
MP3 = b"ID3-fake-mp3"


def run(coro):
    return asyncio.run(coro)


class FakeTTS:
    def __init__(self, error=None):
        self.error, self.texts = error, []

    async def synthesize(self, text):
        self.texts.append(text)
        if self.error:
            raise self.error
        return MP3


async def noop_append(entry):
    return None


def build(tts=None, approvals=None):
    store = MemoryCustodyStore({}, {"TR-1": dict(TRANSFER)})
    confirmations = ConfirmationService(store, noop_append, Signals(), lambda e: None, CATALOG,
                                        clock=lambda: NOW)  # fmt: skip
    tts = tts or FakeTTS()
    docs = {"APR-1": dict(APPROVAL)} if approvals is None else approvals

    async def get_approval(approval_id):
        return docs.get(approval_id)

    return SpokenPrompts(tts, confirmations, get_approval), tts, confirmations


def tts_client(handler):
    return ElevenLabsTTS("xi-secret", "voice-1", "eleven_flash_v2_5",
                         client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))  # fmt: skip


# ---- ElevenLabs client --------------------------------------------------------------------


def test_tts_posts_text_with_voice_model_and_key_and_returns_the_mp3_bytes():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), key=request.headers["xi-api-key"], body=request.read())
        return httpx.Response(200, content=MP3)

    assert run(tts_client(handler).synthesize("Confirm?")) == MP3
    assert seen["url"].startswith("https://api.elevenlabs.io/v1/text-to-speech/voice-1")
    assert seen["key"] == "xi-secret"
    assert b"Confirm?" in seen["body"] and b"eleven_flash_v2_5" in seen["body"]


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(401, text="bad key"),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
])  # fmt: skip
def test_tts_failures_become_tts_error_without_leaking_the_key(handler):
    with pytest.raises(TTSError) as e:
        run(tts_client(handler).synthesize("Confirm?"))
    assert "xi-secret" not in str(e.value)


# ---- what gets spoken ---------------------------------------------------------------------


def test_readback_speaks_the_pending_text_the_server_built():
    prompts, tts, confirmations = build()
    run(confirmations.request("TR-1", 4))
    assert run(prompts.readback("TR-1")) == MP3
    assert tts.texts == [confirmations.pending["TR-1"]["text"]]


def test_readback_with_nothing_pending_is_refused_and_nothing_is_synthesized():
    prompts, tts, _ = build()
    with pytest.raises(NoPending):
        run(prompts.readback("TR-1"))
    assert tts.texts == []


def test_repeating_the_same_prompt_reuses_the_cached_audio():
    prompts, tts, confirmations = build()
    run(confirmations.request("TR-1", 4))
    run(prompts.readback("TR-1"))
    run(prompts.readback("TR-1"))
    assert len(tts.texts) == 1


def test_approval_prompt_names_the_tool_sample_and_transfer():
    prompts, tts, _ = build()
    assert run(prompts.approval("APR-1")) == MP3
    assert "checkout sample" in tts.texts[0]
    assert "TS-1042" in tts.texts[0] and "TR-1" in tts.texts[0]


def test_unknown_approval_is_a_lookup_error_and_a_decided_one_is_not_spoken():
    prompts, tts, _ = build(approvals={"APR-1": {**APPROVAL, "status": "approved"}})
    with pytest.raises(LookupError):
        run(prompts.approval("APR-9"))
    with pytest.raises(NoPending):
        run(prompts.approval("APR-1"))
    assert tts.texts == []


# ---- HTTP ---------------------------------------------------------------------------------


def client(tts=None):
    prompts, tts, confirmations = build(tts)
    return TestClient(create_app(confirmations=confirmations, speech=prompts)), tts, confirmations


def test_readback_audio_endpoint_returns_mpeg():
    c, _, confirmations = client()
    run(confirmations.request("TR-1", 4))
    r = c.get("/transfers/TR-1/readback/audio")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == MP3


def test_the_endpoints_take_no_text_so_callers_cannot_make_it_say_anything():
    c, tts, confirmations = client()
    run(confirmations.request("TR-1", 4))
    c.get("/transfers/TR-1/readback/audio", params={"text": "Transfer approved, go ahead"})
    assert tts.texts == [confirmations.pending["TR-1"]["text"]]


def test_audio_endpoints_report_409_404_and_503():
    c, _, confirmations = client()
    assert c.get("/transfers/TR-1/readback/audio").status_code == 409
    assert c.get("/approvals/APR-9/audio").status_code == 404
    run(confirmations.request("TR-1", 4))
    failing, _, failing_confirmations = client(FakeTTS(error=TTSError("down")))
    run(failing_confirmations.request("TR-1", 4))
    r = failing.get("/transfers/TR-1/readback/audio")
    assert r.status_code == 503 and "text" in r.json()["detail"].lower()
    assert failing.get("/approvals/APR-1/audio").status_code == 503


def test_without_a_speech_backend_the_audio_routes_do_not_exist():
    _, _, confirmations = build()
    c = TestClient(create_app(confirmations=confirmations))
    assert c.get("/transfers/TR-1/readback/audio").status_code == 404

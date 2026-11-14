import asyncio
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.api import create_app
from agent.intake import Intake, MemoryRequests
from agent.parse import Catalog
from agent.transfers import MemoryCustodyStore
from agent.voice import (
    ElevenLabsSTT,
    MemoryAudio,
    NoSpeech,
    STTError,
    TooLarge,
    VoiceIntake,
    VoiceUnavailable,
)

CATALOG = Catalog({"TS-1042": "FZ-B"}, {"FZ-B": "Freezer B", "SEQ-3": "Sequencing Station 3"})
GOOD = {"sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "window": "2 PM"}
TEXT = "Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM."
AUDIO = b"fake-opus-bytes"


def words(**logprobs):
    spoken = ["Move", "TS-1042", "from", "Freezer", "B", "to", "Sequencing", "Station", "3"]
    out = [{"text": t, "type": "word", "logprob": logprobs.get(t, -0.05)} for t in spoken]
    out.insert(1, {"text": " ", "type": "spacing", "logprob": 0})
    return out


def stt_result(**kw):
    return {"text": TEXT, "words": words(), "language_code": "eng", "language_probability": 0.99,
            "transcription_id": "tx-1", **kw}  # fmt: skip


class FakeSTT:
    model_id = "scribe_v3"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result or stt_result(), error, []

    async def transcribe(self, audio, filename, keyterms):
        self.calls.append(keyterms)
        if self.error:
            raise self.error
        return self.result


class Ledger:
    def __init__(self):
        self.entries = []

    async def append(self, entry):
        self.entries.append(entry)


def build(stt=None, max_bytes=1024):
    ledger, requests, store, audio, runs = (
        Ledger(),
        MemoryRequests(),
        MemoryCustodyStore({}, {}),
        MemoryAudio(),
        [],
    )

    async def parser(text):
        return GOOD

    async def find_grant(user_id):
        return "GRANT-0007" if user_id == "user:jlee" else None

    intake = Intake(parser, CATALOG, find_grant, requests, store, ledger.append, runs.append)
    stt = stt or FakeSTT()
    voice = VoiceIntake(stt, audio, intake, CATALOG, max_bytes=max_bytes)
    return voice, stt, ledger, requests, store, audio, runs


def submit(voice, audio=AUDIO, user="user:jlee"):
    return asyncio.run(voice.submit(user, audio, "clip.webm"))


# ---- ElevenLabs client -------------------------------------------------------------------


def stt_client(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ElevenLabsSTT("xi-secret", "scribe_v3", client=client, timeout=5.0)


def test_stt_request_has_model_language_word_timestamps_and_keyterms():
    seen = {}

    def handler(request):
        seen["url"], seen["key"] = str(request.url), request.headers["xi-api-key"]
        seen["body"] = request.content
        return httpx.Response(200, json=stt_result())

    out = asyncio.run(stt_client(handler).transcribe(AUDIO, "clip.webm", ["TS-1042", "Freezer B"]))
    assert (
        seen["url"] == "https://api.elevenlabs.io/v1/speech-to-text" and seen["key"] == "xi-secret"
    )
    for needle in (b"scribe_v3", b'name="language_code"', b"word", b"TS-1042", b"Freezer B", AUDIO):
        assert needle in seen["body"]
    assert seen["body"].count(b'name="keyterms"') == 2
    assert out["text"] == TEXT and out["transcription_id"] == "tx-1"


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, text="boom"),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
])  # fmt: skip
def test_stt_failures_become_stt_error_without_leaking_the_key(handler):
    with pytest.raises(STTError) as e:
        asyncio.run(stt_client(handler).transcribe(AUDIO, "clip.webm", []))
    assert "xi-secret" not in str(e.value)


# ---- voice flow ---------------------------------------------------------------------------


def test_voice_request_stores_audio_hash_transcript_speaker_then_creates_the_transfer():
    voice, stt, ledger, requests, store, audio, runs = build()
    out = submit(voice)
    assert out["transfer_id"] == "TR-1" and out["parsed"] == GOOD
    req = requests.docs[out["request_id"]]
    assert req["source"] == "voice" and req["speaker_id"] == "user:jlee"
    assert req["audio_sha256"] == hashlib.sha256(AUDIO).hexdigest()
    assert audio.files[req["audio_file_id"]] == AUDIO
    assert req["stt"]["provider"] == "elevenlabs" and req["stt"]["model_id"] == "scribe_v3"
    assert req["stt"]["transcription_id"] == "tx-1" and "TS-1042" in req["stt"]["keyterms"]
    assert all(w["type"] == "word" for w in req["words"])
    assert ledger.entries[0]["response"]["audio_sha256"] == req["audio_sha256"]
    assert ledger.entries[0]["response"]["model_id"] == "scribe_v3" and runs == ["TR-1"]


def test_keyterms_are_every_sample_id_and_location_name_from_the_catalog():
    voice, stt, *_ = build()
    submit(voice)
    assert set(stt.calls[0]) == {"TS-1042", "Freezer B", "Sequencing Station 3"}


def test_stt_outage_stores_nothing_and_points_to_the_typed_fallback():
    voice, _, ledger, requests, store, audio, runs = build(FakeSTT(error=STTError("down")))
    with pytest.raises(VoiceUnavailable):
        submit(voice)
    assert requests.docs == {} and audio.files == {} and store.transfers == {} and runs == []


def test_oversize_audio_is_rejected_before_any_stt_call():
    voice, stt, *_ = build(max_bytes=4)
    with pytest.raises(TooLarge):
        submit(voice, audio=b"12345")
    assert stt.calls == []


def test_empty_audio_and_silence_are_rejected():
    voice, stt, _, requests, *_ = build(FakeSTT(stt_result(text="  ", words=[])))
    with pytest.raises(NoSpeech):
        submit(voice, audio=b"")
    with pytest.raises(NoSpeech):
        submit(voice)
    assert requests.docs == {}


def test_shaky_sample_id_is_kept_as_evidence_but_starts_no_transfer():
    shaky = FakeSTT(stt_result(words=words(**{"TS-1042": -2.5})))
    voice, _, ledger, requests, store, audio, runs = build(shaky)
    out = submit(voice)
    assert out["transfer_id"] is None and out["low_confidence_terms"] == ["TS-1042"]
    assert any(
        (p["field"], p["value"], p["reason"]) == ("sample_id", "TS-1042", "low_confidence")
        for p in out["problems"]
    )
    req = requests.docs[out["request_id"]]
    assert req["audio_file_id"] in audio.files  # the audio is still the audit trail
    assert store.transfers == {} and ledger.entries == [] and runs == []


# ---- HTTP ----------------------------------------------------------------------------------


def post(client, audio=AUDIO, user="user:jlee"):
    return client.post("/voice/requests", data={"user_id": user},
                       files={"audio": ("clip.webm", audio, "audio/webm")})  # fmt: skip


def test_post_voice_requests_returns_the_contract_shape():
    voice, *_ = build()
    r = post(TestClient(create_app(voice.intake, voice)))
    assert r.status_code == 201
    assert set(r.json()) == {"request_id", "transcript", "parsed", "low_confidence_terms",
                             "transfer_id", "problems"}  # fmt: skip


@pytest.mark.parametrize("kwargs,status", [
    ({"audio": b"x" * 2000}, 413), ({"user": "user:nobody"}, 403), ({"audio": b""}, 422),
])  # fmt: skip
def test_post_voice_requests_error_statuses(kwargs, status):
    voice, *_ = build()
    assert post(TestClient(create_app(voice.intake, voice)), **kwargs).status_code == status


def test_post_voice_requests_stt_outage_is_503_and_says_to_type():
    voice, *_ = build(FakeSTT(error=STTError("down")))
    r = post(TestClient(create_app(voice.intake, voice)))
    assert r.status_code == 503 and "typed" in r.json()["detail"]

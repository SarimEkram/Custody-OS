"""Voice intake: audio -> ElevenLabs speech-to-text -> the same Intake path as typed requests.

The key stays server-side. The voice grants no authority: the speaker is the logged-in user
passed in by the dashboard, never anything inferred from the audio. If STT fails or is slow,
the caller gets VoiceUnavailable and the UI falls back to the typed box.

shortcut: size is capped in bytes only (1 MiB is roughly the 30 s budget in Opus); the upload
is read fully before the check, so put a body-size limit in front of this for a real deployment.
"""

import hashlib

import httpx

from agent.intake import Intake, NoGrant
from agent.parse import Catalog

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class STTError(Exception):
    pass


class VoiceUnavailable(Exception):
    pass


class TooLarge(Exception):
    pass


class NoSpeech(Exception):
    pass


class ElevenLabsSTT:
    def __init__(
        self,
        api_key: str,
        model_id: str = "scribe_v3",
        client: httpx.AsyncClient | None = None,
        timeout: float = 5.0,
        language_code: str = "en",
    ) -> None:
        self.api_key, self.model_id, self.timeout = api_key, model_id, timeout
        self.language_code = language_code
        self.client = client or httpx.AsyncClient()

    async def transcribe(self, audio: bytes, filename: str, keyterms: list[str]) -> dict:
        try:
            r = await self.client.post(
                STT_URL,
                headers={"xi-api-key": self.api_key},
                files={"file": (filename, audio)},
                data={"model_id": self.model_id, "language_code": self.language_code,
                      "timestamps_granularity": "word", "keyterms": keyterms},
                timeout=self.timeout,
            )  # fmt: skip
        except httpx.HTTPError as e:
            raise STTError(f"speech-to-text request failed: {type(e).__name__}") from e
        if r.status_code != 200:
            raise STTError(f"speech-to-text returned {r.status_code}")
        return r.json()


class MemoryAudio:
    """Stand-in for the GridFS `voice_audio` bucket."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def put(self, data: bytes, filename: str) -> str:
        file_id = f"audio-{len(self.files) + 1}"
        self.files[file_id] = data
        return file_id


class VoiceIntake:
    def __init__(
        self, stt, audio_store, intake: Intake, catalog: Catalog, max_bytes: int = 1_048_576
    ) -> None:
        self.stt, self.audio_store, self.intake = stt, audio_store, intake
        self.catalog, self.max_bytes = catalog, max_bytes

    async def submit(self, user_id: str, audio: bytes, filename: str) -> dict:
        if not audio:
            raise NoSpeech("empty audio")
        if len(audio) > self.max_bytes:
            raise TooLarge(f"audio is {len(audio)} bytes; the limit is {self.max_bytes}")
        if not await self.intake.find_grant(user_id):  # don't spend STT or storage on this
            raise NoGrant(user_id)
        keyterms = [*self.catalog.samples, *self.catalog.locations.values()]
        try:
            heard = await self.stt.transcribe(audio, filename, keyterms)
        except STTError as e:
            raise VoiceUnavailable(str(e)) from e
        text = (heard.get("text") or "").strip()
        if not text:
            raise NoSpeech("no speech detected")
        words = heard.get("words") or []
        ends = [w["end"] for w in words if w.get("end") is not None]
        file_id = await self.audio_store.put(audio, filename)
        return await self.intake.submit(
            user_id, text, source="voice", words=words,
            audio={"file_id": file_id, "sha256": hashlib.sha256(audio).hexdigest(),
                   "duration": max(ends) if ends else None},
            stt={"provider": "elevenlabs", "model_id": self.stt.model_id,
                 "language_code": heard.get("language_code"),
                 "language_probability": heard.get("language_probability"),
                 "keyterms": keyterms, "transcription_id": heard.get("transcription_id")},
        )  # fmt: skip

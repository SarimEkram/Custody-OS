"""Voice intake: audio -> ElevenLabs speech-to-text -> the same Intake path as typed requests.

The key stays server-side. The voice grants no authority: the speaker is the logged-in user
passed in by the dashboard, never anything inferred from the audio. If STT fails or is slow,
the caller gets VoiceUnavailable and the UI falls back to the typed box.

shortcut: size is capped in bytes only (1 MiB is roughly the 30 s budget in Opus); the upload
is read fully before the check, so put a body-size limit in front of this for a real deployment.
"""

import hashlib
from collections.abc import Awaitable, Callable

import httpx

from agent.confirmation import ConfirmationService, NoPending
from agent.intake import Intake, NoGrant
from agent.parse import Catalog

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"


class STTError(Exception):
    pass


class TTSError(Exception):
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


class ElevenLabsTTS:
    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        client: httpx.AsyncClient | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.api_key, self.voice_id, self.model_id = api_key, voice_id, model_id
        self.timeout = timeout
        self.client = client or httpx.AsyncClient()

    async def synthesize(self, text: str) -> bytes:
        try:
            r = await self.client.post(
                TTS_URL.format(voice_id=self.voice_id),
                headers={"xi-api-key": self.api_key},
                json={"text": text, "model_id": self.model_id},
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise TTSError(f"text-to-speech request failed: {type(e).__name__}") from e
        if r.status_code != 200:
            raise TTSError(f"text-to-speech returned {r.status_code}")
        return r.content


class SpokenPrompts:
    """Audio for the prompts the tech and the supervisor must answer.

    Callers pass an id, never text: the words come from the pending read-back or a fixed approval
    template, so what is heard is what will run. Speech is output only and grants no authority.

    shortcut: the audio cache is unbounded; fine while there are only a handful of pending
    prompts per demo run, cap it before a long-lived deployment.
    """

    def __init__(
        self,
        tts,
        confirmations: ConfirmationService,
        get_approval: Callable[[str], Awaitable[dict | None]],
    ) -> None:
        self.tts, self.confirmations, self.get_approval = tts, confirmations, get_approval
        self.cache: dict[str, bytes] = {}

    async def readback(self, transfer_id: str) -> bytes:
        pending = self.confirmations.pending.get(transfer_id)
        if not pending:
            raise NoPending(transfer_id)
        return await self.speak(pending["text"])

    async def approval(self, approval_id: str) -> bytes:
        apr = await self.get_approval(approval_id)
        if apr is None:
            raise LookupError(approval_id)
        if apr["status"] != "pending":
            raise NoPending(approval_id)
        tool = apr["requested_for_tool"].replace("_", " ")
        return await self.speak(
            f"Supervisor approval needed to {tool} sample {apr['sample_id']} for transfer "
            f"{apr['transfer_id']}. Open the approvals inbox to approve or deny."
        )

    async def speak(self, text: str) -> bytes:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key not in self.cache:
            self.cache[key] = await self.tts.synthesize(text)
        return self.cache[key]


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


def sniff_audio_type(head: bytes) -> str:
    """The recording's media type, from its first bytes (the clip is stored without one)."""
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "audio/webm"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[4:8] == b"ftyp":
        return "audio/mp4"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    return "application/octet-stream"

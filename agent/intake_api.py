"""HTTP API the dashboard calls. Typed requests are the always-working fallback for voice."""

import re
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, field_validator

from agent.confirmation import ConfirmationService, NoPending, NotRequester
from agent.intake import Intake, NoGrant
from agent.voice import (
    NoSpeech,
    SpokenPrompts,
    TooLarge,
    TTSError,
    VoiceConfirm,
    VoiceIntake,
    VoiceUnavailable,
    sniff_audio_type,
)


class TypedRequest(BaseModel):
    user_id: str = Field(pattern=r"^user:")
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v.strip()


class ConfirmRequest(BaseModel):
    user_id: str = Field(pattern=r"^user:")
    method: Literal["button", "voice"]


def _byte_range(header: str | None, size: int) -> tuple[int, int] | str | None:
    """One `bytes=a-b`, `a-` or `-n` range. None means send it all (no or odd header)."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    if not m.group(1):  # the last n bytes
        n = int(m.group(2))
        return (max(size - n, 0), size - 1) if n and size else "unsatisfiable"
    start = int(m.group(1))
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    return "unsatisfiable" if start >= size or end < start else (start, end)


def create_app(
    intake: Intake | None = None,
    voice: VoiceIntake | None = None,
    confirmations: ConfirmationService | None = None,
    speech: SpokenPrompts | None = None,
    requests=None,
    audio=None,
    voice_confirm: VoiceConfirm | None = None,
) -> FastAPI:
    app = FastAPI(title="agent")

    def no_grant(e: NoGrant) -> HTTPException:
        return HTTPException(403, f"{e} has no active grant for the agent")

    if intake:

        @app.post("/transfers", status_code=201)
        async def post_transfer(body: TypedRequest) -> dict:
            try:
                return await intake.submit(body.user_id, body.text, source="typed")
            except NoGrant as e:
                raise no_grant(e) from e

    if voice:

        @app.post("/voice/requests", status_code=201)
        async def post_voice(
            user_id: Annotated[str, Form(pattern=r"^user:")],
            audio: Annotated[UploadFile, File()],
        ) -> dict:
            try:
                clip = await audio.read()
                return await voice.submit(user_id, clip, audio.filename or "clip.webm")
            except NoGrant as e:
                raise no_grant(e) from e
            except TooLarge as e:
                raise HTTPException(413, str(e)) from e
            except NoSpeech as e:
                raise HTTPException(422, str(e)) from e
            except VoiceUnavailable as e:
                raise HTTPException(503, "Speech-to-text is unavailable; use the typed box.") from e

    if requests and audio:

        @app.get("/voice/requests/{request_id}/audio")
        async def original_audio(
            request_id: str, rng: Annotated[str | None, Header(alias="Range")] = None
        ) -> Response:
            """Stream the stored recording. Read-only: the audio is evidence."""
            try:
                doc = await requests.get(request_id)
            except KeyError as e:
                raise HTTPException(404, f"unknown request {request_id}") from e
            if not doc.get("audio_file_id"):
                raise HTTPException(404, f"{request_id} was typed: there is no audio recording")
            clip = await audio.get(doc["audio_file_id"])
            size, kind = len(clip), sniff_audio_type(clip[:16])
            span = _byte_range(rng, size)
            if span == "unsatisfiable":
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            if span is None:
                return Response(clip, media_type=kind, headers={"Accept-Ranges": "bytes"})
            start, end = span
            return Response(
                clip[start : end + 1], status_code=206, media_type=kind,
                headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}"},
            )  # fmt: skip

    if confirmations:

        @app.post("/transfers/{transfer_id}/confirm")
        async def confirm(transfer_id: str, body: ConfirmRequest) -> dict:
            try:
                await confirmations.confirm(transfer_id, body.user_id, body.method)
            except KeyError as e:
                raise HTTPException(404, f"unknown transfer {transfer_id}") from e
            except NoPending as e:
                raise HTTPException(409, f"no read-back is waiting on {transfer_id}") from e
            except NotRequester as e:
                raise HTTPException(403, "only the person who made the request can confirm") from e
            return {"confirmed": True}

    if voice_confirm:

        @app.post("/transfers/{transfer_id}/confirm/voice")
        async def confirm_by_voice(
            transfer_id: str,
            user_id: Annotated[str, Form(pattern=r"^user:")],
            audio: Annotated[UploadFile, File()],
        ) -> dict:
            try:
                clip, name = await audio.read(), audio.filename or "confirm.webm"
                return await voice_confirm.submit(transfer_id, user_id, clip, name)
            except KeyError as e:
                raise HTTPException(404, f"unknown transfer {transfer_id}") from e
            except NoPending as e:
                raise HTTPException(409, f"no read-back is waiting on {transfer_id}") from e
            except NotRequester as e:
                raise HTTPException(403, "only the person who made the request can confirm") from e
            except TooLarge as e:
                raise HTTPException(413, str(e)) from e
            except NoSpeech as e:
                raise HTTPException(422, str(e)) from e
            except VoiceUnavailable as e:
                raise HTTPException(503, "Speech-to-text is down; use the Confirm button.") from e

    if speech:

        def mpeg(audio: bytes) -> Response:
            return Response(audio, media_type="audio/mpeg")

        def no_audio(e: TTSError) -> HTTPException:
            return HTTPException(503, "Speech is unavailable; read the text on screen.")

        @app.get("/transfers/{transfer_id}/readback/audio")
        async def readback_audio(transfer_id: str) -> Response:
            try:
                return mpeg(await speech.readback(transfer_id))
            except NoPending as e:
                raise HTTPException(409, f"no read-back is waiting on {transfer_id}") from e
            except TTSError as e:
                raise no_audio(e) from e

        @app.get("/approvals/{approval_id}/audio")
        async def approval_audio(approval_id: str) -> Response:
            try:
                return mpeg(await speech.approval(approval_id))
            except LookupError as e:
                raise HTTPException(404, f"unknown approval {approval_id}") from e
            except NoPending as e:
                raise HTTPException(409, f"{approval_id} is not waiting for a decision") from e
            except TTSError as e:
                raise no_audio(e) from e

    return app

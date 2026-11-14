"""HTTP API the dashboard calls. Typed requests are the always-working fallback for voice."""

from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, field_validator

from agent.confirmation import ConfirmationService, NoPending, NotRequester
from agent.intake import Intake, NoGrant
from agent.voice import (
    NoSpeech,
    SpokenPrompts,
    TooLarge,
    TTSError,
    VoiceIntake,
    VoiceUnavailable,
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


def create_app(
    intake: Intake | None = None,
    voice: VoiceIntake | None = None,
    confirmations: ConfirmationService | None = None,
    speech: SpokenPrompts | None = None,
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

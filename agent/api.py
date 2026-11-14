"""HTTP API the dashboard calls. Typed requests are the always-working fallback for voice."""

from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from agent.intake import Intake, NoGrant
from agent.voice import NoSpeech, TooLarge, VoiceIntake, VoiceUnavailable


class TypedRequest(BaseModel):
    user_id: str = Field(pattern=r"^user:")
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v.strip()


def create_app(intake: Intake, voice: VoiceIntake | None = None) -> FastAPI:
    app = FastAPI(title="agent")

    @app.post("/transfers", status_code=201)
    async def post_transfer(body: TypedRequest) -> dict:
        try:
            return await intake.submit(body.user_id, body.text, source="typed")
        except NoGrant as e:
            raise HTTPException(403, f"{e} has no active grant for the agent") from e

    if voice:

        @app.post("/voice/requests", status_code=201)
        async def post_voice(
            user_id: Annotated[str, Form(pattern=r"^user:")],
            audio: Annotated[UploadFile, File()],
        ) -> dict:
            try:
                return await voice.submit(
                    user_id, await audio.read(), audio.filename or "clip.webm"
                )
            except NoGrant as e:
                raise HTTPException(403, f"{e} has no active grant for the agent") from e
            except TooLarge as e:
                raise HTTPException(413, str(e)) from e
            except NoSpeech as e:
                raise HTTPException(422, str(e)) from e
            except VoiceUnavailable as e:
                raise HTTPException(503, "Speech-to-text is unavailable; use the typed box.") from e

    return app

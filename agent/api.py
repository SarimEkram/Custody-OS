"""HTTP API the dashboard calls. Typed requests are the always-working fallback for voice."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from agent.intake import Intake, NoGrant


class TypedRequest(BaseModel):
    user_id: str = Field(pattern=r"^user:")
    text: str

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v.strip()


def create_app(intake: Intake) -> FastAPI:
    app = FastAPI(title="agent")

    @app.post("/transfers", status_code=201)
    async def post_transfer(body: TypedRequest) -> dict:
        try:
            return await intake.submit(body.user_id, body.text, source="typed")
        except NoGrant as e:
            raise HTTPException(403, f"{e} has no active grant for the agent") from e

    return app

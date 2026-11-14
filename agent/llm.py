"""Chat client for an OpenAI-compatible endpoint (Groq free tier by default).

Plain httpx, no SDK. The free tier is rate-limited, so one 429 is retried after the
advertised wait; a second one is an LLMError (the caller falls back to DEMO_SCRIPTED).
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx


class LLMError(Exception):
    pass


class ChatLLM:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        retry_wait: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        # GROQ_API_KEY may hold several comma-separated keys: calls alternate between them, so the
        # per-key free-tier limits add up, and a 429 on one key is retried on the next at once.
        self.keys = [k.strip() for k in api_key.split(",") if k.strip()]
        self.turn = 0
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=30)
        self.retry_wait, self.sleep = retry_wait, sleep

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        body = {"model": self.model, "messages": messages, "tools": tools,
                "tool_choice": "auto", "temperature": 0}  # fmt: skip
        r = await self._post(body)
        for _ in self.keys[1:]:  # another key may still have budget
            if r.status_code != 429:
                break
            r = await self._post(body)
        if r.status_code == 429:
            await self.sleep(float(r.headers.get("retry-after", self.retry_wait)))
            r = await self._post(body)
        if r.status_code == 429:
            raise LLMError("rate limit (429) after one retry")
        if r.status_code >= 400:
            raise LLMError(f"LLM returned {r.status_code}: {r.text[:200]}")
        return r.json()["choices"][0]["message"]

    async def _post(self, body: dict) -> httpx.Response:
        key = self.keys[self.turn % len(self.keys)]
        self.turn += 1
        return await self.client.post(
            self.url, json=body, headers={"Authorization": f"Bearer {key}"}
        )

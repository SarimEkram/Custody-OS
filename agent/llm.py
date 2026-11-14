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
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=30)
        self.retry_wait, self.sleep = retry_wait, sleep

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        body = {"model": self.model, "messages": messages, "tools": tools,
                "tool_choice": "auto", "temperature": 0}  # fmt: skip
        r = await self.client.post(self.url, json=body, headers=self.headers)
        if r.status_code == 429:
            await self.sleep(float(r.headers.get("retry-after", self.retry_wait)))
            r = await self.client.post(self.url, json=body, headers=self.headers)
        if r.status_code == 429:
            raise LLMError("rate limit (429) after one retry")
        if r.status_code >= 400:
            raise LLMError(f"LLM returned {r.status_code}: {r.text[:200]}")
        return r.json()["choices"][0]["message"]

import asyncio
import json

import httpx
import pytest

from agent.llm import ChatLLM, LLMError

REPLY = {"choices": [{"message": {"role": "assistant", "content": "hi", "tool_calls": None}}]}


def make(handler, sleeps=None):
    async def sleep(s):
        if sleeps is not None:
            sleeps.append(s)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ChatLLM("https://llm.test/v1", "sekret", "m-1", client=client, sleep=sleep)


def test_request_shape_and_returned_message():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=REPLY)

    msg = asyncio.run(
        make(handler).chat([{"role": "user", "content": "x"}], [{"type": "function"}])
    )
    assert msg["content"] == "hi"
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sekret"
    assert seen["body"]["model"] == "m-1" and seen["body"]["temperature"] == 0
    assert seen["body"]["tools"] == [{"type": "function"}]


def test_one_429_is_retried_after_the_advertised_wait():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={})
        return httpx.Response(200, json=REPLY)

    asyncio.run(make(handler, sleeps).chat([], []))
    assert len(calls) == 2 and sleeps == [3.0]


def test_a_second_429_is_an_llm_error():
    with pytest.raises(LLMError, match="rate limit"):
        asyncio.run(make(lambda r: httpx.Response(429, json={})).chat([], []))


def test_server_errors_raise_without_leaking_the_key():
    with pytest.raises(LLMError) as e:
        asyncio.run(make(lambda r: httpx.Response(500, text="boom")).chat([], []))
    assert "500" in str(e.value) and "sekret" not in str(e.value)

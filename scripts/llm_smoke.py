"""Live check: the LLM key and model work and answer with a well-formed tool call.

.venv/bin/python scripts/llm_smoke.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, ".")
from agent.llm import ChatLLM, LLMError  # noqa: E402
from agent.tool_catalogue import openai_tools  # noqa: E402
from scripts._env import load_env  # noqa: E402


async def main() -> int:
    load_env()
    llm = ChatLLM(os.environ["LLM_BASE_URL"], os.environ["GROQ_API_KEY"], os.environ["LLM_MODEL"])
    tools = openai_tools()
    try:
        msg = await llm.chat(
            [
                {
                    "role": "system",
                    "content": "You move lab samples using the tools. Call one tool.",
                },
                {"role": "user", "content": "Validated: move TS-1042 from FZ-B to SEQ-3. Start."},
            ],
            tools,
        )
    except LLMError as e:
        print(f"FAIL  {e}")
        return 1
    calls = msg.get("tool_calls") or []
    if not calls:
        print(f"FAIL  no tool call; model said: {msg.get('content')!r}")
        return 1
    args = json.loads(calls[0]["function"]["arguments"])
    ok = "reason" in args
    name = calls[0]["function"]["name"]
    print(f"{'PASS' if ok else 'FAIL'}  {name}({args})  [{len(tools)} tools offered]")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))

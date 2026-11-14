"""DEMO_SCRIPTED=1: replay a recorded model run so a demo never depends on the API.

Only the model is replaced. The loop, gate, executor, lab services and scanner stay real, so
every decision, scan and ledger entry still happens. rule_parser stands in for the model's
parse step on the fixed demo phrasings (no model call at all).
"""

import json
import re
from pathlib import Path

from agent.intake import ParseFailed
from agent.parse import Catalog

SAMPLE_ID = re.compile(r"\b([A-Za-z]{2}-\d{4})\b")
WINDOW = re.compile(r"\bfor (\d{1,2}(?::\d{2})? ?[ap]\.?m\.?)", re.IGNORECASE)
FINISHED = {"role": "assistant", "content": "Script finished.", "tool_calls": None}


def rule_parser(catalog: Catalog):
    async def parse(text: str) -> dict:
        found = SAMPLE_ID.search(text)
        if not found:
            raise ParseFailed("no sample id in the request")
        sample = found.group(1).upper()
        lowered = text.lower()
        places = sorted(
            (lowered.find(name.lower()), loc)
            for loc, name in catalog.locations.items()
            if name.lower() in lowered
        )
        ordered = [loc for _, loc in places]
        parsed: dict = {"sample_id": sample}
        if len(ordered) >= 2:
            parsed["from"], parsed["to"] = ordered[0], ordered[1]
        elif len(ordered) == 1:
            parsed["to"] = ordered[0]
            if sample in catalog.samples:
                parsed["from"] = catalog.samples[sample]
        if window := WINDOW.search(text):
            parsed["window"] = window.group(1).upper().replace(".", "")
        return parsed

    return parse


class ScriptedLLM:
    def __init__(self, messages: list[dict]) -> None:
        self.script = list(messages)

    @classmethod
    def load(cls, path: str | Path) -> "ScriptedLLM":
        return cls(json.loads(Path(path).read_text()))

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        return self.script.pop(0) if self.script else FINISHED


class RecordingLLM:
    """Wraps the live model and keeps every reply, so a good run can be replayed."""

    def __init__(self, inner) -> None:
        self.inner, self.messages = inner, []

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        reply = await self.inner.chat(messages, tools)
        self.messages.append(reply)
        return reply

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.messages, indent=2))

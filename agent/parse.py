"""parse_request: never act on a guessed sample ID or location (CLAUDE.md).

The LLM proposes {sample_id, from, to, window}; this checks it against the catalog (what the
DB holds) and against the STT word confidence. Anything doubtful becomes a problem the agent
must put back to the tech instead of proceeding.
"""

import difflib
import re
from dataclasses import dataclass, field

from agent.executor import RunContext

FIELDS = ("sample_id", "from", "to", "window")


@dataclass
class Catalog:
    samples: dict[str, str]  # sample_id -> freezer location where it is stored
    locations: dict[str, str]  # location_id -> spoken name ("Freezer B")


@dataclass
class ParseResult:
    parsed: dict
    problems: list[dict] = field(default_factory=list)
    low_confidence_terms: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


LABELS = {"sample_id": "sample ID", "from": "source location", "to": "destination"}


def _suggest(field_name: str, reason: str, value: str | None, catalog: Catalog) -> list[str]:
    if reason == "low_confidence":
        return [value] if value else []
    if reason not in ("unknown_sample", "unknown_location") or not value:
        return []
    options = catalog.samples if field_name == "sample_id" else catalog.locations
    return difflib.get_close_matches(value, list(options), n=3, cutoff=0.6)


def _either(options: list[str]) -> str:
    return options[0] if len(options) == 1 else ", ".join(options[:-1]) + " or " + options[-1]


def _message(
    field_name: str, reason: str, value, suggestions: list[str], clean: dict, catalog: Catalog
) -> str:
    if reason in ("unknown_sample", "unknown_location"):
        text = f"I can't find {value}."
        return f"{text} Did you mean {_either(suggestions)}?" if suggestions else text
    if reason == "low_confidence":
        return f"I'm not sure I heard {value} correctly. Did you mean {value}?"
    if reason == "sample_not_at_source":
        where = catalog.locations[catalog.samples[clean["sample_id"]]]
        return f"{clean['sample_id']} is in {where}, not {catalog.locations[value]}."
    if reason == "same_as_source":
        return "The destination is the same as the source."
    return f"I didn't catch the {LABELS.get(field_name, field_name)}."


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9\-]+", text.lower()))


def validate_parse(
    parsed: dict, catalog: Catalog, words: list[dict] | None, threshold: float = -1.0
) -> ParseResult:
    clean = {k: parsed[k] for k in FIELDS if parsed.get(k)}
    result = ParseResult(parsed=clean)

    def problem(field_name: str, reason: str) -> None:
        value = clean.get(field_name)
        suggestions = _suggest(field_name, reason, value, catalog)
        message = _message(field_name, reason, value, suggestions, clean, catalog)
        result.problems.append({"field": field_name, "value": value, "reason": reason,
                                "suggestions": suggestions, "message": message})  # fmt: skip

    sample, src, dst = clean.get("sample_id"), clean.get("from"), clean.get("to")
    if not sample:
        problem("sample_id", "missing")
    elif sample not in catalog.samples:
        problem("sample_id", "unknown_sample")
    for name, loc in (("from", src), ("to", dst)):
        if not loc:
            problem(name, "missing")
        elif loc not in catalog.locations:
            problem(name, "unknown_location")
    if sample in catalog.samples and src in catalog.locations and catalog.samples[sample] != src:
        problem("from", "sample_not_at_source")
    if src and src == dst:
        problem("to", "same_as_source")

    if words:
        spoken = {
            "sample_id": _tokens(sample or ""),
            "from": _tokens(catalog.locations.get(src, "")),
            "to": _tokens(catalog.locations.get(dst, "")),
        }
        flagged: set[str] = set()
        for w in words:
            if w.get("type", "word") != "word" or w.get("logprob", 0) >= threshold:
                continue
            for name, toks in spoken.items():
                if _tokens(w["text"]) & toks:
                    flagged.add(name)
                    if w["text"] not in result.low_confidence_terms:
                        result.low_confidence_terms.append(w["text"])
        for name in ("sample_id", "from", "to"):
            if name in flagged:
                problem(name, "low_confidence")
    return result


def make_parse_handler(catalog: Catalog, words: list[dict] | None, threshold: float = -1.0):
    """Executor handler for the internal parse_request tool. Records refs only if valid."""

    async def handle(args: dict, ctx: RunContext) -> dict:
        result = validate_parse(args, catalog, words, threshold)
        if result.ok:
            ctx.refs.update(result.parsed)
            return {"ok": True, "parsed": result.parsed}
        return {
            "ok": False,
            "problems": result.problems,
            "low_confidence_terms": result.low_confidence_terms,
            "message": "Do not proceed. Ask the tech to repeat or choose; never guess.",
        }

    return handle

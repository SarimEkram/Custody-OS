import asyncio
import json

import pytest

from agent.intake import ParseFailed
from agent.parse import Catalog
from agent.scripted import RecordingLLM, ScriptedLLM, rule_parser
from tests.test_loop import HAPPY, world

CATALOG = Catalog(
    {"TS-1042": "FZ-B", "TS-1077": "FZ-A", "EV-2201": "FZ-B"},
    {
        "FZ-A": "Freezer A",
        "FZ-B": "Freezer B",
        "SEQ-3": "Sequencing Station 3",
        "FOR-LAB": "Forensics Lab",
    },
)


def run(coro):
    return asyncio.run(coro)


def parse(text):
    return run(rule_parser(CATALOG)(text))


def test_beat_one_phrasing_is_parsed_without_a_model():
    assert parse("Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM.") == {
        "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "window": "2 PM",
    }  # fmt: skip


def test_when_only_a_destination_is_spoken_the_source_is_where_the_sample_is_stored():
    assert parse("Move EV-2201 to the Forensics Lab now.") == {
        "sample_id": "EV-2201", "from": "FZ-B", "to": "FOR-LAB",
    }  # fmt: skip


def test_beat_three_phrasing_and_case_insensitivity():
    got = parse("move ts-1077 from freezer a to sequencing station 3")
    assert got == {"sample_id": "TS-1077", "from": "FZ-A", "to": "SEQ-3"}


def test_a_mistyped_id_is_passed_through_so_validation_can_catch_it():
    assert parse("Move TS-1024 from Freezer B to Sequencing Station 3")["sample_id"] == "TS-1024"


def test_no_sample_id_at_all_is_not_understood():
    with pytest.raises(ParseFailed):
        parse("Please move the thing to the other place")


def test_scripted_llm_replays_in_order_ignores_the_prompt_then_finishes():
    llm = ScriptedLLM([{"role": "assistant", "content": "one", "tool_calls": None}])
    first = run(llm.chat([{"role": "user", "content": "anything"}], []))
    assert first["content"] == "one"
    last = run(llm.chat([], []))
    assert last["tool_calls"] is None and last["content"]  # exhausted: it stops, never loops


def test_a_live_run_can_be_recorded_saved_and_replayed(tmp_path):
    rec = RecordingLLM(ScriptedLLM(HAPPY))  # stands in for the live model
    loop, _, ledger, store, ctx, _ = world([])
    loop.llm = rec
    first = asyncio.run(loop.run("go", ctx))
    assert first.status == "done" and len(rec.messages) == len(HAPPY)

    path = tmp_path / "beat1.json"
    rec.save(path)
    assert json.loads(path.read_text()) == rec.messages

    loop2, _, ledger2, store2, ctx2, _ = world([])
    loop2.llm = ScriptedLLM.load(path)
    replay = asyncio.run(loop2.run("go", ctx2))
    assert replay.status == "done" and store2.transfers["TR-1"]["state"] == "received"
    assert [e["entry_type"] for e in ledger2.entries] == [e["entry_type"] for e in ledger.entries]

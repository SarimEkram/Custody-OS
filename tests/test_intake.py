import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from agent.intake import Intake, MemoryRequests, NoGrant, ParseFailed, llm_parser
from agent.intake_api import create_app
from agent.parse import Catalog
from agent.transfers import MemoryCustodyStore

CATALOG = Catalog({"TS-1042": "FZ-B"}, {"FZ-B": "Freezer B", "SEQ-3": "Sequencing Station 3"})
GOOD = {"sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "window": "2 PM"}
TEXT = "Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM."


class Ledger:
    def __init__(self):
        self.entries = []

    async def append(self, entry):
        self.entries.append(entry)
        return len(self.entries)


def build(proposed=GOOD, grant="GRANT-0007"):
    ledger, requests, store, runs = Ledger(), MemoryRequests(), MemoryCustodyStore({}, {}), []

    async def parser(text):
        if isinstance(proposed, Exception):
            raise proposed
        return proposed

    async def find_grant(user_id):
        return grant if user_id == "user:jlee" else None

    intake = Intake(parser, CATALOG, find_grant, requests, store, ledger.append, runs.append)
    return intake, ledger, requests, store, runs


def submit(intake, text=TEXT, user="user:jlee"):
    return asyncio.run(intake.submit(user, text))


def test_valid_typed_request_creates_request_transfer_intake_entry_and_starts_the_run():
    intake, ledger, requests, store, runs = build()
    out = submit(intake)
    assert out == {
        "request_id": "VR-0001", "transcript": TEXT, "parsed": GOOD,
        "low_confidence_terms": [], "transfer_id": "TR-0001", "problems": [],
    }  # fmt: skip
    req = requests.docs["VR-0001"]
    assert (req["source"], req["speaker_id"], req["audio_file_id"], req["stt"]) == (
        "typed", "user:jlee", None, None,
    )  # fmt: skip
    assert req["transcript"] == TEXT and req["transfer_id"] == "TR-0001"
    tr = store.transfers["TR-0001"]
    assert (tr["state"], tr["requested_by"], tr["grant_id"], tr["request_id"]) == (
        "requested", "user:jlee", "GRANT-0007", "VR-0001",
    )  # fmt: skip
    assert (tr["sample_id"], tr["from"], tr["to"], tr["source"]) == (
        "TS-1042",
        "FZ-B",
        "SEQ-3",
        "typed",
    )
    assert tr["readback_confirmed"] is False
    [entry] = ledger.entries
    assert (
        entry["entry_type"] == "intake" and entry["tool"] is None and entry["decision"] == "allow"
    )
    assert (entry["actor"], entry["on_behalf_of"]) == ("user:jlee", "user:jlee")
    assert (entry["request_id"], entry["transfer_id"]) == ("VR-0001", "TR-0001")
    assert entry["response"]["transcript"] == TEXT and entry["correlation_id"]
    assert runs == ["TR-0001"]


def test_invalid_request_is_recorded_but_creates_no_transfer_and_starts_nothing():
    intake, ledger, requests, store, runs = build({**GOOD, "sample_id": "TS-1024"})
    out = submit(intake)
    assert out["transfer_id"] is None and out["problems"][0]["reason"] == "unknown_sample"
    assert requests.docs["VR-0001"]["transfer_id"] is None
    assert store.transfers == {} and ledger.entries == [] and runs == []


def test_unparseable_request_is_a_problem_not_a_crash():
    intake, *_ = build(ParseFailed("no tool call"))
    out = submit(intake)
    assert out["transfer_id"] is None and out["problems"][0]["reason"] == "not_understood"


def test_speaker_without_an_active_grant_is_refused_and_nothing_is_stored():
    intake, ledger, requests, store, runs = build()
    with pytest.raises(NoGrant):
        submit(intake, user="user:nobody")
    assert requests.docs == {} and store.transfers == {} and runs == []


def test_ids_are_sequential_per_request_and_transfer():
    intake, *_ = build()
    assert submit(intake)["transfer_id"] == "TR-0001"
    assert submit(intake)["request_id"] == "VR-0002"


def test_post_transfers_returns_the_voice_contract_shape():
    intake, *_ = build()
    client = TestClient(create_app(intake))
    r = client.post("/transfers", json={"user_id": "user:jlee", "text": TEXT})
    assert r.status_code == 201
    assert set(r.json()) == {"request_id", "transcript", "parsed", "low_confidence_terms",
                             "transfer_id", "problems"}  # fmt: skip


def test_post_transfers_rejects_bad_input_and_ungranted_users():
    intake, *_ = build()
    client = TestClient(create_app(intake))
    assert client.post("/transfers", json={"user_id": "jlee", "text": TEXT}).status_code == 422
    assert client.post("/transfers", json={"user_id": "user:jlee", "text": " "}).status_code == 422
    assert (
        client.post("/transfers", json={"user_id": "user:nobody", "text": TEXT}).status_code == 403
    )


def test_llm_parser_extracts_the_parse_request_arguments_and_drops_the_reason():
    call = {"id": "1", "type": "function", "function": {
        "name": "parse_request", "arguments": json.dumps({**GOOD, "reason": "r"})}}  # fmt: skip

    class LLM:
        async def chat(self, messages, tools):
            assert [t["function"]["name"] for t in tools] == ["parse_request"]
            assert "Freezer B" in messages[0]["content"]  # the model is told the real locations
            return {"role": "assistant", "content": None, "tool_calls": [call]}

    assert asyncio.run(llm_parser(LLM(), CATALOG)(TEXT)) == GOOD


def test_llm_parser_raises_when_the_model_makes_no_tool_call():
    class LLM:
        async def chat(self, messages, tools):
            return {"role": "assistant", "content": "I do not know", "tool_calls": None}

    with pytest.raises(ParseFailed):
        asyncio.run(llm_parser(LLM(), CATALOG)(TEXT))

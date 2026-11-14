import asyncio

from agent.executor import RunContext
from agent.parse import Catalog, make_parse_handler, validate_parse

CATALOG = Catalog(
    samples={"TS-1042": "FZ-B", "TS-1077": "FZ-A", "EV-2201": "FZ-B"},
    locations={"FZ-A": "Freezer A", "FZ-B": "Freezer B", "SEQ-3": "Sequencing Station 3"},
)
GOOD = {"sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "window": "2 PM"}


def words(**logprobs):
    """Words of 'Move TS-1042 from Freezer B to Sequencing Station 3', chosen logprobs."""
    text = ["Move", "TS-1042", "from", "Freezer", "B", "to", "Sequencing", "Station", "3"]
    return [{"text": t, "type": "word", "logprob": logprobs.get(t, -0.05)} for t in text]


def problems(result):
    return {(p["field"], p["reason"]) for p in result.problems}


def test_valid_request_passes_with_normalised_fields():
    r = validate_parse(GOOD, CATALOG, words())
    assert r.ok and r.problems == [] and r.low_confidence_terms == []
    assert r.parsed == GOOD


def test_unknown_sample_is_a_problem():
    r = validate_parse({**GOOD, "sample_id": "TS-1024"}, CATALOG, None)
    assert not r.ok and ("sample_id", "unknown_sample") in problems(r)


def test_unknown_locations_are_problems():
    r = validate_parse({**GOOD, "from": "NOPE", "to": "ALSO-NOPE"}, CATALOG, None)
    assert {("from", "unknown_location"), ("to", "unknown_location")} <= problems(r)


def test_sample_must_actually_be_at_the_source():
    r = validate_parse({**GOOD, "from": "FZ-A"}, CATALOG, None)
    assert ("from", "sample_not_at_source") in problems(r)


def test_source_and_destination_must_differ():
    r = validate_parse({**GOOD, "to": "FZ-B"}, CATALOG, None)
    assert ("to", "same_as_source") in problems(r)


def test_missing_fields_are_problems_but_window_is_optional():
    r = validate_parse({"sample_id": "TS-1042", "from": "FZ-B"}, CATALOG, None)
    assert ("to", "missing") in problems(r) and ("window", "missing") not in problems(r)


def test_low_confidence_sample_id_blocks_even_though_it_exists():
    r = validate_parse(GOOD, CATALOG, words(**{"TS-1042": -2.0}))
    assert not r.ok and ("sample_id", "low_confidence") in problems(r)
    assert r.low_confidence_terms == ["TS-1042"]


def test_low_confidence_in_a_location_name_flags_that_field():
    r = validate_parse(GOOD, CATALOG, words(Freezer=-1.5))
    assert ("from", "low_confidence") in problems(r)


def test_shaky_words_unrelated_to_the_request_are_ignored():
    r = validate_parse(GOOD, CATALOG, words(Move=-3.0))
    assert r.ok and r.low_confidence_terms == []


def test_typed_requests_have_no_confidence_to_check():
    assert validate_parse(GOOD, CATALOG, None).ok


def test_handler_records_refs_only_when_the_request_is_valid():
    handler = make_parse_handler(CATALOG, words())
    ctx = RunContext("agent:custody-01", "user:jlee", "GRANT-0007", "TR-1")
    out = asyncio.run(handler({**GOOD, "reason": "r"}, ctx))
    assert out["ok"] is True
    parsed = {"sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "window": "2 PM"}
    assert ctx.refs == {"transfer_id": "TR-1", **parsed}

    bad_ctx = RunContext("agent:custody-01", "user:jlee", "GRANT-0007", "TR-2")
    out = asyncio.run(handler({**GOOD, "sample_id": "TS-1024", "reason": "r"}, bad_ctx))
    assert out["ok"] is False and out["problems"]
    assert bad_ctx.refs == {"transfer_id": "TR-2"}  # nothing from the bad parse was recorded


def one(result, field_name):
    return next(p for p in result.problems if p["field"] == field_name)


def test_unknown_sample_offers_the_closest_real_ids():
    p = one(validate_parse({**GOOD, "sample_id": "TS-1024"}, CATALOG, None), "sample_id")
    assert p["suggestions"][0] == "TS-1042" and "EV-2201" not in p["suggestions"]
    assert p["message"].startswith("I can't find TS-1024. Did you mean TS-1042")


def test_unknown_location_offers_the_closest_real_locations():
    p = one(validate_parse({**GOOD, "from": "FZ-C"}, CATALOG, None), "from")
    assert "FZ-B" in p["suggestions"] and "SEQ-3" not in p["suggestions"]
    assert "I can't find FZ-C" in p["message"]


def test_nothing_close_means_no_suggestion_and_no_dangling_question():
    p = one(validate_parse({**GOOD, "sample_id": "ZZZ"}, CATALOG, None), "sample_id")
    assert p["suggestions"] == [] and p["message"] == "I can't find ZZZ."


def test_shaky_hearing_asks_the_tech_to_confirm_what_it_thinks_it_heard():
    p = one(validate_parse(GOOD, CATALOG, words(**{"TS-1042": -2.0})), "sample_id")
    assert p["suggestions"] == ["TS-1042"]
    assert p["message"] == "I'm not sure I heard TS-1042 correctly. Did you mean TS-1042?"


def test_missing_and_inconsistent_requests_get_plain_messages():
    r = validate_parse({"sample_id": "TS-1042", "from": "FZ-A"}, CATALOG, None)
    assert one(r, "to")["message"] == "I didn't catch the destination."
    assert one(r, "from")["message"] == "TS-1042 is in Freezer B, not Freezer A."
    r = validate_parse({**GOOD, "to": "FZ-B"}, CATALOG, None)
    assert one(r, "to")["message"] == "The destination is the same as the source."

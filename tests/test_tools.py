import pytest

from agent.tool_catalogue import TOOLS, irreversible_tools, openai_tools

LLM_TOOLS = {
    "parse_request", "request_confirmation", "lookup_sample", "reserve_station",
    "request_courier", "notify", "checkout_sample", "handoff_to_courier", "deliver",
}  # fmt: skip


def test_llm_sees_exactly_the_documented_tools_and_never_the_compensations():
    names = {t["function"]["name"] for t in openai_tools()}
    assert names == LLM_TOOLS
    assert not {"cancel_reservation", "cancel_courier_job", "return_to_storage"} & names


def test_every_tool_requires_a_reason_and_is_in_openai_function_format():
    for t in openai_tools():
        fn = t["function"]
        assert t["type"] == "function" and fn["description"]
        assert "reason" in fn["parameters"]["required"]
        assert set(fn["parameters"]["required"]) <= set(fn["parameters"]["properties"])


def test_reversibility_tags_match_the_catalogue():
    assert irreversible_tools() == {"checkout_sample", "handoff_to_courier", "deliver"}
    for name in ("reserve_station", "request_courier", "notify"):
        assert TOOLS[name].reversible is True
    for name in ("parse_request", "request_confirmation", "lookup_sample"):
        assert TOOLS[name].reversible is None  # read-only


def test_expected_scan_per_physical_tool():
    expected = {t.name: t.expected_scan for t in TOOLS.values() if t.expected_scan}
    assert expected == {
        "checkout_sample": "checkout",
        "handoff_to_courier": "handoff",
        "deliver": "intake",
        "return_to_storage": "checkin",
    }


@pytest.mark.parametrize("name", ["reserve_station", "request_courier", "notify"])
def test_reversible_tools_name_their_compensation(name):
    comp = TOOLS[name].compensation
    assert comp in TOOLS or comp == "retract_notice"


@pytest.mark.parametrize("name", ["checkout_sample", "handoff_to_courier", "deliver"])
def test_irreversible_tools_describe_a_recovery_step(name):
    assert TOOLS[name].recovery

"""The seeded history must look like a real run, or the dashboard shows 'Running' forever and the
auditor reports false gaps. Pure checks on the seed's own definitions (no database)."""

from agent.tools import TOOLS
from db.seed import DENIED_TEXT, HISTORY_STEPS


def test_every_history_step_is_a_known_tool_with_the_right_reversibility():
    for tool, reversible, summary, scan in HISTORY_STEPS:
        assert tool in TOOLS
        assert TOOLS[tool].reversible is reversible  # the gap query keys on reversible == False
        assert tool in summary
        assert (scan is not None) == (TOOLS[tool].expected_scan is not None)  # physical steps carry a scan


def test_each_physical_step_has_the_scan_type_the_verifier_expects():
    for tool, _rev, _text, scan in HISTORY_STEPS:
        if scan:
            assert scan[0] == TOOLS[tool].expected_scan


def test_the_denied_request_has_its_own_transfer_id():
    # TR-0002 used to be a ledger-only id, so the next real transfer reused it and inherited the denial
    import inspect

    import db.seed as seed
    src = inspect.getsource(seed.seed_history)
    assert '"_id": "TR-0002"' in src and "VR-0002" in src
    assert "EV-2201" in DENIED_TEXT

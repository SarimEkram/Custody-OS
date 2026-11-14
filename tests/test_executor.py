import asyncio
import itertools

import pytest

from agent.executor import Executor, RunContext, ToolCall
from agent.gate_stub import GateDecision, StubGate
from agent.lab import LabError

REQUIRED = {
    "timestamp", "entry_type", "actor", "on_behalf_of", "policy_id", "policy_version",
    "decision", "transfer_id", "correlation_id",
}  # fmt: skip


class Ledger:
    def __init__(self):
        self.entries, self._ids = [], itertools.count(1)

    async def append(self, entry):
        self.entries.append(entry)
        return len(self.entries)


class Lab:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    async def call(self, tool, args, correlation_id, refs):
        self.calls.append((tool, correlation_id, len(ledger_len_probe)))
        if self.error:
            raise self.error
        return {"ok": True, "tool": tool}


class Verifier:
    def __init__(self, result="confirmed"):
        self.result, self.calls = result, []

    async def confirm(self, *, correlation_id, tool, ctx, outcome_id):
        self.calls.append((correlation_id, tool, outcome_id))
        return self.result


class Gate:
    """Behaves like the real gate: writes its decision entry, then answers."""

    def __init__(self, ledger, decision):
        self.ledger, self.decision, self.seen = ledger, decision, []

    async def check(self, **kw):
        self.seen.append(kw)
        lid = await self.ledger.append(
            {**{k: kw[k] for k in ("transfer_id", "correlation_id")}, "entry_type": "decision",
             "decision": self.decision}
        )  # fmt: skip
        return GateDecision(decision=self.decision, policy_id="p", policy_version=1, ledger_id=lid)


ledger_len_probe: list = []
CTX = dict(actor="agent:custody-01", on_behalf_of="user:jlee", grant_id="GRANT-0007",
           transfer_id="TR-1", request_id="VR-1")  # fmt: skip


def ctx():
    return RunContext(**CTX, refs={"sample_id": "TS-1042"})


def make(decision="allow", lab=None, verifier=None, internal=None):
    ledger = Ledger()
    ledger_len_probe.clear()
    ledger_len_probe.extend([])
    gate = Gate(ledger, decision)
    ex = Executor(gate=gate, lab=lab or Lab(), append=ledger.append, verifier=verifier,
                  internal=internal or {}, new_cid=iter(["c-1", "c-2", "c-3"]).__next__)  # fmt: skip
    return ex, ledger, gate


def call(name, **args):
    return ToolCall(id="t1", name=name, args={"reason": "because", **args})


def run(coro):
    return asyncio.run(coro)


def test_allowed_call_writes_decision_before_acting_then_outcome_referencing_it():
    ex, ledger, _ = make()
    res = run(ex.execute(call("lookup_sample", sample_id="TS-1042"), ctx()))
    assert res.status == "OK" and res.content == {"ok": True, "tool": "lookup_sample"}
    assert [e["entry_type"] for e in ledger.entries] == ["decision", "outcome"]
    assert ledger.entries[1]["refs"] == [1]


def test_decision_is_in_the_ledger_before_the_lab_is_called():
    ledger = Ledger()

    class CheckingLab(Lab):
        async def call(self, tool, args, cid, refs):
            assert [e["entry_type"] for e in ledger.entries] == ["decision"]
            return {}

    ex = Executor(gate=Gate(ledger, "allow"), lab=CheckingLab(), append=ledger.append,
                  new_cid=lambda: "c-1")  # fmt: skip
    assert run(ex.execute(call("lookup_sample", sample_id="TS-1042"), ctx())).status == "OK"


@pytest.mark.parametrize("decision,status", [
    ("deny", "DENIED"), ("pending_approval", "PENDING_APPROVAL"),
    ("pending_confirmation", "PENDING_CONFIRMATION"), ("halt", "HALTED"),
])  # fmt: skip
def test_blocked_calls_never_reach_the_lab_and_write_no_outcome(decision, status):
    lab = Lab()
    ex, ledger, _ = make(decision, lab=lab)
    res = run(ex.execute(call("checkout_sample", sample_id="TS-1042"), ctx()))
    assert res.status == status
    assert lab.calls == []
    assert [e["entry_type"] for e in ledger.entries] == ["decision"]


def test_one_correlation_id_ties_gate_lab_and_ledger_together():
    lab = Lab()
    ex, ledger, gate = make(lab=lab)
    res = run(ex.execute(call("lookup_sample", sample_id="TS-1042"), ctx()))
    assert res.correlation_id == "c-1"
    assert gate.seen[0]["correlation_id"] == "c-1" and lab.calls[0][1] == "c-1"
    assert {e["correlation_id"] for e in ledger.entries} == {"c-1"}


def test_outcome_entry_carries_every_validator_required_field():
    ex, ledger, _ = make()
    run(ex.execute(call("checkout_sample", sample_id="TS-1042"), ctx()))
    outcome = ledger.entries[1]
    assert REQUIRED <= outcome.keys()
    assert outcome["tool"] == "checkout_sample" and outcome["reversible"] is False
    assert outcome["status"] == "sent" and outcome["request_id"] == "VR-1"
    assert outcome["actor"] == "agent:custody-01" and outcome["on_behalf_of"] == "user:jlee"


def test_non_physical_tools_have_no_sent_status_and_no_verification():
    verifier = Verifier()
    ex, ledger, _ = make(verifier=verifier)
    run(ex.execute(call("lookup_sample", sample_id="TS-1042"), ctx()))
    assert ledger.entries[1]["status"] is None and verifier.calls == []


def test_physical_tools_are_verified_by_a_scan_not_by_the_agent():
    verifier = Verifier("unconfirmed")
    ex, _, _ = make(verifier=verifier)
    res = run(ex.execute(call("checkout_sample", sample_id="TS-1042"), ctx()))
    assert verifier.calls == [("c-1", "checkout_sample", 2)]
    assert res.status == "OK" and res.verification == "unconfirmed"


def test_lab_failure_is_recorded_and_returned_as_error_without_verification():
    verifier = Verifier()
    ex, ledger, _ = make(lab=Lab(LabError(404, "not stored")), verifier=verifier)
    res = run(ex.execute(call("checkout_sample", sample_id="TS-1042"), ctx()))
    assert res.status == "ERROR" and "not stored" in str(res.content)
    assert ledger.entries[1]["response"]["error"] == "404: not stored"
    assert ledger.entries[1]["status"] is None and verifier.calls == []


def test_internal_tools_use_their_handler_and_are_still_gated_and_logged():
    async def notify(args, ctx):
        return {"sent_to": args["user_id"]}

    lab = Lab()
    ex, ledger, _ = make(lab=lab, internal={"notify": notify})
    res = run(ex.execute(call("notify", user_id="user:jlee", message="hi"), ctx()))
    assert res.content == {"sent_to": "user:jlee"} and lab.calls == []
    assert [e["entry_type"] for e in ledger.entries] == ["decision", "outcome"]


def test_internal_tool_without_a_handler_is_an_error():
    ex, _, _ = make()
    assert run(ex.execute(call("notify", user_id="u", message="m"), ctx())).status == "ERROR"


def test_stub_gate_always_allows_and_logs_its_decision_first():
    ledger = Ledger()
    d = run(StubGate(ledger.append).check(correlation_id="c-9", transfer_id="TR-1", **{
        k: v for k, v in {**CTX, "tool": "lookup_sample", "args": {}, "reason": "r"}.items()
        if k not in ("transfer_id", "request_id")}))  # fmt: skip
    assert d.decision == "allow" and d.policy_id == "STUB"
    assert (
        ledger.entries[0]["entry_type"] == "decision" and ledger.entries[0]["decision"] == "allow"
    )

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agent.executor import RunContext, ToolResult
from agent.gate_stub import GateDecision, StubGate
from agent.recovery import RecoveryPlanner, choose_freezer
from agent.transfers import MemoryCustodyStore
from tests.test_loop import HAPPY, tc, world

T0 = datetime(2026, 11, 14, 19, 0, tzinfo=UTC)
CERTS = {"FZ-A": {"BSL-2"}, "FZ-B": {"BSL-2", "FORENSIC"}}


def run(coro):
    return asyncio.run(coro)


# ---- choose_freezer: pure ------------------------------------------------------------------


def test_fastest_certified_freezer_within_budget_wins():
    assert choose_freezer({"FZ-A": 1.0, "FZ-B": 2.0}, CERTS, set(), 5.0) == ("FZ-A", True)


def test_an_uncertified_freezer_is_never_chosen_even_if_closer():
    assert choose_freezer({"FZ-A": 1.0, "FZ-B": 4.0}, CERTS, {"FORENSIC"}, 5.0) == ("FZ-B", True)


def test_if_nothing_is_reachable_in_time_it_still_picks_the_fastest_and_says_late():
    assert choose_freezer({"FZ-A": 6.0, "FZ-B": 9.0}, CERTS, set(), 3.0) == ("FZ-A", False)


def test_no_certified_freezer_at_all_is_none():
    assert choose_freezer({"FZ-A": 1.0}, CERTS, {"FORENSIC"}, 5.0) == (None, False)


# ---- planner with a fake executor ----------------------------------------------------------


class Ledger:
    def __init__(self):
        self.entries = []

    async def append(self, entry):
        self.entries.append(entry)


class Executor:
    def __init__(self, verification="confirmed"):
        self.calls, self.verification = [], verification

    async def execute(self, call, ctx):
        self.calls.append((call.name, call.args, ctx.on_behalf_of, ctx.outcome_type))
        return ToolResult(tool_call_id=call.id, status="OK", correlation_id="c-r",
                          verification=self.verification if call.name == "return_to_storage" else None)  # fmt: skip


class Bus:
    def __init__(self, scanned_at):
        self.scans = {"c-r": {"scanned_at": scanned_at}}


OUTCOMES = [
    {"entry_type": "outcome", "tool": "reserve_station", "reversible": True,
     "response": {"reservation_id": "RES-1"}},
    {"entry_type": "outcome", "tool": "request_courier", "reversible": True,
     "response": {"job_id": "CJ-1"}},
    {"entry_type": "outcome", "tool": "notify", "reversible": True,
     "args": {"user_id": "user:jlee"}, "response": {"sent": True}},
]  # fmt: skip


def planner(*, sample_status="stored", job_status="assigned", progress=0.0, verification="confirmed",
            scanned_after_min=2.0, max_out=10, outcomes=OUTCOMES):  # fmt: skip
    store = MemoryCustodyStore(
        {"TS-1042": {"status": sample_status, "checked_out_at": T0, "max_minutes_out": max_out,
                     "required_certifications": []}},
        {"TR-1": {"_id": "TR-1", "state": "in_transit", "history": []}},
    )  # fmt: skip
    ledger, ex = Ledger(), Executor(verification)

    async def read_outcomes(tid):
        return outcomes

    async def job_state(jid):
        return {"status": job_status, "progress": progress, "origin": "FZ-B",
                "destination": "SEQ-3", "eta_minutes": 4.0}  # fmt: skip

    bus = Bus(T0 + timedelta(seconds=scanned_after_min))
    p = RecoveryPlanner(ex, store, ledger.append, read_outcomes, job_state, CERTS, bus,
                        demo_speed=60.0, clock=lambda: T0 + timedelta(seconds=progress * 4))  # fmt: skip
    ctx = RunContext("agent:custody-01", "user:jlee", "GRANT-0007", "TR-1", "VR-1",
                     refs={"sample_id": "TS-1042", "courier_job_id": "CJ-1"})  # fmt: skip
    return p, ex, store, ledger, ctx


def test_sample_never_left_the_freezer_so_reversible_actions_are_rolled_back_newest_first():
    p, ex, store, ledger, ctx = planner()
    result = run(p.run(ctx, revoker="user:mokafor"))
    assert [c[0] for c in ex.calls] == ["notify", "cancel_courier_job", "cancel_reservation"]
    assert (
        ex.calls[0][1]["user_id"] == "user:jlee" and "retract" in ex.calls[0][1]["message"].lower()
    )
    assert ex.calls[1][1] == {"job_id": "CJ-1", "reason": ex.calls[1][1]["reason"]}
    assert {c[2] for c in ex.calls} == {"user:mokafor"}  # recovery acts for the revoker
    assert {c[3] for c in ex.calls} == {"compensation"}
    assert result.status == "cancelled" and store.transfers["TR-1"]["state"] == "cancelled"


def test_a_courier_that_already_has_the_sample_is_rerouted_not_cancelled():
    p, ex, *_ = planner(sample_status="out", job_status="in_transit", progress=0.5)
    run(p.run(*[RunContext("agent:custody-01", "user:jlee", "G", "TR-1", "VR-1",
                           refs={"sample_id": "TS-1042", "courier_job_id": "CJ-1"})],
              revoker="user:mokafor"))  # fmt: skip
    names = [c[0] for c in ex.calls]
    assert "cancel_courier_job" not in names and names[-1] == "return_to_storage"


def test_sample_out_goes_back_to_the_nearest_certified_freezer_and_exposure_is_logged():
    p, ex, store, ledger, ctx = planner(sample_status="out", job_status="in_transit", progress=0.5)
    result = run(p.run(ctx, revoker="user:mokafor"))
    name, args, who, kind = ex.calls[-1]
    assert (name, args["freezer_id"], who, kind) == (
        "return_to_storage",
        "FZ-B",
        "user:mokafor",
        "recovery",
    )
    assert ctx.refs["return_to"] == "FZ-B" and store.transfers["TR-1"]["state"] == "recovering"
    assert result.status == "recovered" and result.freezer_id == "FZ-B" and not result.exceeded
    [exposure] = [e for e in ledger.entries if e["entry_type"] == "exposure"]
    assert exposure["response"] == {
        "minutes_out": pytest.approx(2.0),
        "exceeded": False,
        "limit": 10,
    }


def test_clock_run_out_is_a_violation_and_the_sample_is_flagged_compromised():
    p, ex, store, ledger, ctx = planner(sample_status="out", job_status="in_transit", progress=0.5,
                                        scanned_after_min=4.0, max_out=3)  # fmt: skip
    result = run(p.run(ctx, revoker="user:mokafor"))
    assert result.status == "recovered_late" and result.exceeded
    assert store.samples["TS-1042"]["status"] == "compromised"
    kinds = [e["entry_type"] for e in ledger.entries]
    assert "exposure" in kinds and "violation" in kinds


def test_an_unconfirmed_return_is_a_violation_not_a_silent_success():
    p, ex, store, ledger, ctx = planner(sample_status="out", job_status="in_transit",
                                        progress=0.5, verification="unconfirmed")  # fmt: skip
    result = run(p.run(ctx, revoker="user:mokafor"))
    assert result.status == "unconfirmed"
    assert [e["entry_type"] for e in ledger.entries] == ["violation"]


# ---- the revoke-mid-transfer beat, against the real services -------------------------------


class HaltOn:
    """Stub gate that withdraws authority at one tool; everything else is allowed."""

    def __init__(self, inner, tool):
        self.inner, self.tool = inner, tool

    async def check(self, **kw):
        if kw["tool"] == self.tool:
            lid = await self.inner.append({"entry_type": "decision", "decision": "halt", **{
                k: kw[k] for k in ("transfer_id", "correlation_id")}})  # fmt: skip
            return GateDecision(decision="halt", policy_id="p", policy_version=1, ledger_id=lid)
        return await self.inner.check(**kw)


def test_revoked_in_the_corridor_is_compensated_returned_confirmed_and_audited():
    loop, llm, ledger, store, ctx, notes = world(HAPPY[:-2] + HAPPY[-2:-1])
    store.samples["TS-1042"].update(max_minutes_out=10, required_certifications=[])
    loop.executor.gate = HaltOn(StubGate(ledger.append), tool="deliver")
    parts = loop.parts

    result = asyncio.run(loop.run("move it", ctx))
    assert result.status == "halted" and store.transfers["TR-1"]["state"] == "in_transit"

    async def read_outcomes(tid):
        return [e for e in ledger.entries if e["entry_type"] == "outcome"]

    async def job_state(jid):
        return (await parts["lab"].courier.get(f"/jobs/{jid}")).json()

    planner = RecoveryPlanner(loop.executor, store, ledger.append, read_outcomes, job_state,
                              {"FZ-B": {"BSL-2"}}, parts["bus"], demo_speed=60.0)  # fmt: skip
    outcome = asyncio.run(planner.run(ctx, revoker="user:jlee"))

    assert outcome.status == "recovered" and outcome.freezer_id == "FZ-B"
    assert store.transfers["TR-1"]["state"] == "recovered"
    assert (
        store.samples["TS-1042"]["status"] == "stored"
        and store.samples["TS-1042"]["location_id"] == "FZ-B"
    )
    kinds = [e["entry_type"] for e in ledger.entries]
    assert kinds.count("compensation") == 2 and kinds.count("recovery") == 1  # notify, reservation
    assert "exposure" in kinds and "violation" not in kinds
    assert [e["status"] for e in ledger.entries if e["entry_type"] == "verification"][
        -1
    ] == "confirmed"

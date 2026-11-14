"""B's seams onto Person A's gate/custody (injected fakes: no Atlas needed)."""

import asyncio
import json
from dataclasses import replace

from agent.atlas import AtlasExecutor, ScanApplier
from agent.confirmation import ConfirmationService, NotRequester
from agent.executor import RunContext, ToolCall
from agent.loop import AgentLoop, RunResult
from agent.parse import Catalog
from agent.policy_gate import ToolResult as GateResult
from agent.recovery import RecoveryPlanner
from agent.runner import Runner
from agent.signals import Signals
from agent.transfers import MemoryCustodyStore
from agent.verifier import ScanBus


def run(coro):
    return asyncio.run(coro)


CTX = RunContext("agent:custody-01", "user:jlee", "GRANT-0007", "TR-0002", "VR-0002",
                 refs={"sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3"})  # fmt: skip


def gate_result(status="OK", tool="lookup_sample", **kw):
    return GateResult(status=status, tool=tool, correlation_id="c-1", message=status, **kw)


class FakeGate:
    """Stands in for policy_gate.execute: records the call, optionally runs the executor."""

    def __init__(self, result, run_executor=True):
        self.result, self.run_executor, self.calls, self.executed = result, run_executor, [], None

    async def __call__(self, tool, args, reason, ctx, executor, **kw):
        self.calls.append((tool, args, reason, ctx))
        if self.run_executor and self.result.status == "OK":
            self.executed = await executor(tool, args, "c-1")
        return self.result


class Lab:
    def __init__(self):
        self.calls = []

    async def call(self, tool, args, cid, refs):
        self.calls.append((tool, args, cid))
        return {"ok": True}


def adapter(gate, bus=None, timeout=0.05, internal=None, transfers=None):
    recorded = []

    async def get_transfer(tid):
        return {"_id": tid}

    async def record_unverified(transfer, scan, status, detail, correlation_id=None):
        recorded.append((transfer["_id"], str(status), correlation_id))

    lab = Lab()
    ex = AtlasExecutor(lab, internal or {}, bus or ScanBus(), execute=gate, get_transfer=get_transfer,
                       record_unverified=record_unverified, next_state={"checkout": "checked_out"},
                       timeout=timeout)  # fmt: skip
    return ex, lab, recorded


def call(name="lookup_sample", **args):
    return ToolCall(id="t1", name=name, args={"reason": "because", **args})


# ---- AtlasExecutor ---------------------------------------------------------------------


def test_the_call_goes_through_the_gate_with_reason_split_out_and_the_lab_runs_only_via_it():
    gate = FakeGate(gate_result(response={"location_id": "FZ-B"}))
    ex, lab, _ = adapter(gate)
    res = run(ex.execute(call(sample_id="TS-1042"), CTX))
    tool, args, reason, ctx = gate.calls[0]
    assert (tool, args, reason) == ("lookup_sample", {"sample_id": "TS-1042"}, "because")
    assert (ctx.transfer_id, ctx.on_behalf_of, ctx.grant_id, ctx.request_id, ctx.mode) == (
        "TR-0002", "user:jlee", "GRANT-0007", "VR-0002", "normal",
    )  # fmt: skip
    assert lab.calls == [("lookup_sample", {"sample_id": "TS-1042"}, "c-1")]
    assert (
        res.status == "OK" and res.content["location_id"] == "FZ-B" and res.correlation_id == "c-1"
    )


def test_recovery_runs_use_the_gates_recovery_mode():
    gate = FakeGate(gate_result(tool="cancel_reservation"))
    ex, *_ = adapter(gate)
    run(ex.execute(call("cancel_reservation", reservation_id="RES-1"),
                   replace(CTX, outcome_type="compensation")))  # fmt: skip
    assert gate.calls[0][3].mode == "recovery"


def test_internal_tools_run_their_handler_not_the_lab():
    async def notify(args, ctx):
        return {"sent_to": args["user_id"]}

    gate = FakeGate(gate_result(tool="notify"))
    ex, lab, _ = adapter(gate, internal={"notify": notify})
    run(ex.execute(call("notify", user_id="user:jlee", message="hi"), CTX))
    assert lab.calls == [] and gate.executed == {"sent_to": "user:jlee"}


def test_blocked_results_carry_the_approval_id_and_never_reach_the_lab():
    gate = FakeGate(gate_result("PENDING_APPROVAL", tool="checkout_sample", approval_id="APR-9",
                                rule_hits=["forensic_requires_supervisor"]), run_executor=False)  # fmt: skip
    ex, lab, _ = adapter(gate)
    res = run(ex.execute(call("checkout_sample", sample_id="EV-2201"), CTX))
    assert res.status == "PENDING_APPROVAL" and res.content["approval_id"] == "APR-9"
    assert res.content["rule_hits"] == ["forensic_requires_supervisor"] and lab.calls == []


def physical(bus, state):
    gate = FakeGate(gate_result(tool="checkout_sample", expected_scan="checkout"))
    bus.on_scan(
        {"correlation_id": "c-1", "type": "checkout", "_result": {"state": state, "error": None}}
    )
    return adapter(gate, bus)


def test_a_physical_step_is_confirmed_only_once_the_scan_has_moved_the_transfer():
    ex, _, recorded = physical(ScanBus(), "checked_out")
    res = run(ex.execute(call("checkout_sample", sample_id="TS-1042"), CTX))
    assert res.status == "OK" and res.verification == "confirmed" and recorded == []


def test_a_scan_that_did_not_advance_the_transfer_is_a_mismatch():
    ex, *_ = physical(ScanBus(), "reserved")  # the custody check refused it (e.g. bad signature)
    res = run(ex.execute(call("checkout_sample", sample_id="TS-1042"), CTX))
    assert res.verification == "mismatch"


def test_no_scan_in_time_is_unconfirmed_and_recorded_through_the_custody_module():
    gate = FakeGate(gate_result(tool="checkout_sample", expected_scan="checkout"))
    ex, _, recorded = adapter(gate)
    res = run(ex.execute(call("checkout_sample", sample_id="TS-1042"), CTX))
    assert res.verification == "unconfirmed" and recorded == [("TR-0002", "unconfirmed", "c-1")]


def test_non_physical_steps_do_not_wait_for_a_scan():
    ex, _, recorded = adapter(FakeGate(gate_result()))
    res = run(ex.execute(call(sample_id="TS-1042"), CTX))
    assert res.verification is None and recorded == []


# ---- ScanApplier -----------------------------------------------------------------------


def test_the_applier_applies_each_scan_once_and_publishes_the_result():
    seen = []

    async def apply(scan):
        seen.append(scan["correlation_id"])
        return "checked_out"

    applier = ScanApplier(apply)
    run(applier.handle({"correlation_id": "c-7", "type": "checkout"}))
    assert seen == ["c-7"] and applier.bus.scans["c-7"]["_result"] == {
        "state": "checked_out",
        "error": None,
    }


def test_an_apply_failure_is_published_not_raised_so_the_watcher_keeps_running():
    async def apply(scan):
        raise LookupError("no active transfer")

    applier = ScanApplier(apply)
    run(applier.handle({"correlation_id": "c-8", "type": "checkout"}))
    result = applier.bus.scans["c-8"]["_result"]
    assert result["state"] is None and "no active transfer" in result["error"]


# ---- seams in the loop, confirmation, recovery and runner ------------------------------


class Store(MemoryCustodyStore):
    def __init__(self):
        super().__init__({}, {"TR-0002": {"_id": "TR-0002", "state": "reserved", "history": [],
                                          "blocked_from": "reserved"}})  # fmt: skip
        self.blocked = []

    async def block(self, tid, reason):
        self.blocked.append(("block", tid))

    async def unblock(self, tid):
        self.blocked.append(("unblock", tid))


def test_when_the_gate_owns_blocking_the_loop_only_waits_and_retries():
    from tests.test_loop_blocking import LLM, Executor, say, tc

    llm = LLM(say(tc(1, "checkout_sample", sample_id="S")), say(content="done"))
    ex = Executor(checkout_sample=[("PENDING_APPROVAL", {"approval_id": "APR-1"}), ("OK", {})])
    store, signals = Store(), Signals()
    loop = AgentLoop(llm, ex, store, signals=signals, wait_timeout=1.0, manage_blocking=False)

    async def go():
        asyncio.get_running_loop().call_later(0.03, signals.signal, "approval:APR-1", "approved")
        return await loop.run("go", CTX)

    assert asyncio.run(go()).status == "done"
    assert ex.calls == ["checkout_sample", "checkout_sample"] and store.blocked == []


CATALOG = Catalog({"TS-1042": "FZ-B"}, {"FZ-B": "Freezer B", "SEQ-3": "Sequencing Station 3"})


def test_confirmation_can_hand_the_recording_to_the_gate_and_still_wake_the_agent():
    store = Store()
    store.transfers["TR-0002"].update(sample_id="TS-1042", **{"from": "FZ-B", "to": "SEQ-3"},
                                      requested_by="user:jlee")  # fmt: skip
    recorded, appended, signals = [], [], Signals()

    async def recorder(tid, user, text, method):
        recorded.append((tid, user, text, method))

    svc = ConfirmationService(
        store, appended.append, signals, lambda e: None, CATALOG, recorder=recorder
    )
    out = run(svc.request("TR-0002", 4))
    run(svc.confirm("TR-0002", "user:jlee", "voice"))
    assert recorded == [("TR-0002", "user:jlee", out["readback_text"], "voice")]
    assert appended == [] and run(signals.wait("confirm:TR-0002", 0.05)) == "confirmed"


def test_the_gates_permission_error_becomes_not_requester():
    store = Store()
    store.transfers["TR-0002"].update(sample_id="TS-1042", **{"from": "FZ-B", "to": "SEQ-3"},
                                      requested_by="user:jlee")  # fmt: skip

    async def recorder(tid, user, text, method):
        raise PermissionError("only the requesting tech can confirm the read-back")

    svc = ConfirmationService(
        store, lambda e: None, Signals(), lambda e: None, CATALOG, recorder=recorder
    )
    run(svc.request("TR-0002", 4))
    try:
        run(svc.confirm("TR-0002", "user:x", "button"))
        raise AssertionError("expected NotRequester")
    except NotRequester:
        pass


def test_recovery_leaves_exposure_and_compromise_to_the_custody_module_when_told_to():
    from tests.test_recovery import Bus, Executor as Ex, Ledger, T0

    store = MemoryCustodyStore(
        {"TS-1042": {"status": "out", "checked_out_at": T0, "max_minutes_out": 10,
                     "required_certifications": []}},
        {"TR-0002": {"_id": "TR-0002", "state": "in_transit", "history": []}},
    )  # fmt: skip
    ledger, ex = Ledger(), Ex()

    async def outcomes(tid):
        return []

    async def job(jid):
        return {"status": "in_transit", "progress": 0.5, "origin": "FZ-B", "destination": "SEQ-3",
                "eta_minutes": 4.0}  # fmt: skip

    async def after_checkin():  # custody's apply_verified_scan has just stored the sample
        store.samples["TS-1042"]["status"] = "compromised"

    class Late(Ex):
        async def execute(self, c, ctx):
            r = await super().execute(c, ctx)
            if c.name == "return_to_storage":
                await after_checkin()
            return r

    planner = RecoveryPlanner(Late(), store, ledger.append, outcomes, job, {"FZ-B": {"BSL-2"}},
                              Bus(T0), log_exposure=False)  # fmt: skip
    out = run(
        planner.run(replace(CTX, refs={**CTX.refs, "courier_job_id": "CJ-1"}), "user:mokafor")
    )
    assert ledger.entries == []  # no exposure/violation rows from B: A's custody wrote them
    assert out.status == "recovered_late" and out.exceeded


def test_a_halt_cancels_a_run_that_is_mid_step_and_recovery_runs_exactly_once():
    store = MemoryCustodyStore({}, {"TR-0002": {
        "_id": "TR-0002", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3",
        "requested_by": "user:jlee", "grant_id": "G", "request_id": "VR-0002", "state": "in_transit",
        "history": []}})  # fmt: skip
    requests = type("R", (), {"docs": {"VR-0002": {"transcript": "move it"}}})()
    started, calls = asyncio.Event(), []

    class SlowLoop:
        async def run(self, text, ctx):
            started.set()
            await asyncio.sleep(
                30
            )  # e.g. deliver() waiting on a courier that is still in the corridor
            return RunResult("done")

    class Planner:
        async def run(self, ctx, revoker):
            calls.append(revoker)
            return "recovered"

    runner = Runner(SlowLoop(), Planner(), store, requests)

    async def go():
        task = runner.start("TR-0002")
        await started.wait()
        await runner.halt("TR-0002", "user:mokafor")
        await runner.halt("TR-0002", "user:mokafor")  # a second halt event changes nothing
        return await task

    out = asyncio.run(go())
    assert calls == ["user:mokafor"] and out.run.status == "halted" and out.recovery == "recovered"
    assert json.dumps(runner.errors) == "{}"

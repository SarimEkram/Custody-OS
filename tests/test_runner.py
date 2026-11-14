import asyncio

import pytest

from agent.loop import RunResult
from agent.runner import Runner
from agent.transfers import MemoryCustodyStore

TRANSFER = {"_id": "TR-1", "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "window": "2 PM",
            "requested_by": "user:jlee", "grant_id": "GRANT-0007", "request_id": "VR-1",
            "state": "requested", "history": []}  # fmt: skip


class Requests:
    docs = {"VR-1": {"transcript": "Move TS-1042 to Sequencing Station 3."}}

    async def get(self, request_id):
        return self.docs[request_id]


REQUESTS = Requests()


class Loop:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.ran = result or RunResult("done", "ok"), error, []

    async def run(self, text, ctx):
        self.ran.append((text, ctx))
        if self.error:
            raise self.error
        return self.result


class Planner:
    def __init__(self):
        self.calls = []

    async def run(self, ctx, revoker):
        self.calls.append((ctx.transfer_id, revoker))
        return "recovered"


def build(result=None, error=None, **transfer_extra):
    store = MemoryCustodyStore({}, {"TR-1": {**TRANSFER, **transfer_extra}})
    loop, planner = Loop(result, error), Planner()
    return Runner(loop, planner, store, REQUESTS), loop, planner


def run(runner, tid="TR-1"):
    return asyncio.run(runner.run(tid))


def test_the_run_context_comes_from_the_transfer_and_the_agent_acts_for_the_requester():
    runner, loop, _ = build()
    run(runner)
    text, ctx = loop.ran[0]
    assert (ctx.actor, ctx.on_behalf_of, ctx.grant_id) == (
        "agent:custody-01",
        "user:jlee",
        "GRANT-0007",
    )
    assert (ctx.transfer_id, ctx.request_id) == ("TR-1", "VR-1")
    assert (
        ctx.refs["sample_id"] == "TS-1042"
        and ctx.refs["from"] == "FZ-B"
        and ctx.refs["to"] == "SEQ-3"
    )
    assert "Move TS-1042 to Sequencing Station 3." in text and "TS-1042" in text and "FZ-B" in text


def test_a_normal_finish_runs_no_recovery():
    runner, _, planner = build(state="received")
    out = run(runner)
    assert out.run.status == "done" and out.recovery is None and planner.calls == []


def test_a_halt_runs_recovery_for_whoever_revoked():
    runner, _, planner = build(RunResult("halted"), halted_by="user:mokafor")
    out = run(runner)
    assert planner.calls == [("TR-1", "user:mokafor")] and out.recovery == "recovered"


def test_a_halt_without_a_recorded_revoker_falls_back_to_the_requester():
    runner, _, planner = build(RunResult("halted"))
    run(runner)
    assert planner.calls == [("TR-1", "user:jlee")]


def test_denial_recovers_only_if_something_irreversible_happened():
    runner, _, planner = build(RunResult("denied", needs_recovery=True))
    run(runner)
    assert len(planner.calls) == 1
    runner, _, planner = build(RunResult("denied", needs_recovery=False), state="cancelled")
    run(runner)
    assert planner.calls == []


@pytest.mark.parametrize("state", ["requested", "reserved"])
def test_a_run_that_ends_before_anything_moved_is_rolled_back_and_closed(state):
    # e.g. the model gave up after a denial: without this the transfer looks busy for ever
    runner, _, planner = build(RunResult("done"), state=state)
    out = run(runner)
    assert planner.calls == [("TR-1", "user:jlee")] and out.recovery == "recovered"


def test_background_start_runs_the_transfer_and_keeps_a_crash_instead_of_swallowing_it():
    async def go(runner):
        task = runner.start("TR-1")
        await asyncio.wait([task])  # finished, without re-raising a crash here
        return runner

    ok = asyncio.run(go(build()[0]))
    assert ok.errors == {}
    boom, loop, _ = build(error=RuntimeError("model exploded"))
    failed = asyncio.run(go(boom))
    assert str(failed.errors["TR-1"]) == "model exploded"


def test_a_request_without_a_time_tells_the_model_the_window_is_asap():
    # otherwise the model stops to ask for a time window, which no one can answer mid-run
    runner, loop, _ = build(window=None)
    run(runner)
    assert "window ASAP" in loop.ran[0][0]
    runner, loop, _ = build(window="2 PM")
    run(runner)
    assert "window 2 PM" in loop.ran[0][0] and "ASAP" not in loop.ran[0][0]

import asyncio
import json

from agent.executor import RunContext, ToolResult
from agent.loop import AgentLoop
from agent.signals import Signals
from agent.transfers import MemoryCustodyStore


def tc(n, name, **args):
    arguments = json.dumps({"reason": "r", **args})
    return {"id": f"call_{n}", "type": "function",
            "function": {"name": name, "arguments": arguments}}  # fmt: skip


def say(*calls, content=None):
    return {"role": "assistant", "content": content, "tool_calls": list(calls) or None}


class LLM:
    def __init__(self, *msgs):
        self.script, self.seen = list(msgs), []

    async def chat(self, messages, tools):
        self.seen.append(len(messages))
        return self.script.pop(0)


class Executor:
    """Returns scripted results per tool name and records every call."""

    def __init__(self, **results):
        self.results, self.calls = {k: list(v) for k, v in results.items()}, []

    async def execute(self, call, ctx):
        self.calls.append(call.name)
        status, content = self.results[call.name].pop(0)
        return ToolResult(tool_call_id=call.id, status=status, content=content)


class Confirmations:
    def __init__(self, pending=()):
        self.pending = {t: {} for t in pending}


def world(llm, executor, state="reserved", pending=(), timeout=1.0):
    store = MemoryCustodyStore({}, {"TR-1": {"_id": "TR-1", "state": state, "history": []}})
    signals = Signals()
    loop = AgentLoop(llm, executor, store, signals=signals, confirmations=Confirmations(pending),
                     wait_timeout=timeout)  # fmt: skip
    return loop, store, signals, RunContext("agent:custody-01", "user:jlee", "G", "TR-1", "VR-1")


def go(loop, ctx, *timers):
    async def main():
        for delay, fn in timers:
            asyncio.get_running_loop().call_later(delay, fn)
        return await loop.run("go", ctx)

    return asyncio.run(main())


def test_pending_confirmation_waits_then_retries_the_same_call_without_asking_the_model():
    llm = LLM(say(tc(1, "checkout_sample", sample_id="S")), say(content="done"))
    ex = Executor(checkout_sample=[("PENDING_CONFIRMATION", {}), ("OK", {"ok": 1})])
    loop, store, signals, ctx = world(llm, ex, pending=["TR-1"])
    seen_state = []

    def confirm():
        seen_state.append(
            (store.transfers["TR-1"]["state"], store.transfers["TR-1"]["blocked_reason"])
        )
        signals.signal("confirm:TR-1", "confirmed")

    result = go(loop, ctx, (0.03, confirm))
    assert result.status == "done" and ex.calls == ["checkout_sample", "checkout_sample"]
    assert seen_state == [("blocked", "readback_pending")]
    assert store.transfers["TR-1"]["state"] == "reserved"  # restored once unblocked
    assert len(llm.seen) == 2  # one proposal, one wrap-up: waiting costs no model calls


def test_pending_approval_waits_on_that_approval_id():
    llm = LLM(say(tc(1, "checkout_sample", sample_id="S")), say(content="done"))
    ex = Executor(checkout_sample=[("PENDING_APPROVAL", {"approval_id": "APR-1"}), ("OK", {})])
    loop, store, signals, ctx = world(llm, ex)
    result = go(loop, ctx, (0.03, lambda: signals.signal("approval:APR-1", "approved")))
    assert result.status == "done" and ex.calls == ["checkout_sample"] * 2


def test_missing_readback_is_requested_by_the_loop_before_waiting():
    llm = LLM(say(tc(1, "checkout_sample", sample_id="S")), say(content="done"))
    ex = Executor(checkout_sample=[("PENDING_CONFIRMATION", {}), ("OK", {})],
                  request_confirmation=[("OK", {"readback_text": "..."})])  # fmt: skip
    loop, _, signals, ctx = world(llm, ex, pending=[])  # the model never asked
    go(loop, ctx, (0.03, lambda: signals.signal("confirm:TR-1", "confirmed")))
    assert ex.calls == ["checkout_sample", "request_confirmation", "checkout_sample"]


def test_denied_approval_ends_the_run_and_cancels_when_nothing_irreversible_happened():
    llm = LLM(say(tc(1, "checkout_sample", sample_id="S")), say(content="never reached"))
    ex = Executor(checkout_sample=[("PENDING_APPROVAL", {"approval_id": "APR-1"})])
    loop, store, signals, ctx = world(llm, ex, state="reserved")
    result = go(loop, ctx, (0.03, lambda: signals.signal("approval:APR-1", "denied")))
    assert result.status == "denied" and result.needs_recovery is False
    assert store.transfers["TR-1"]["state"] == "cancelled" and ex.calls == ["checkout_sample"]
    assert len(llm.seen) == 1


def test_denial_after_the_sample_is_out_flags_recovery_instead_of_cancelling():
    llm = LLM(say(tc(1, "deliver", transfer_id="TR-1")))
    ex = Executor(deliver=[("PENDING_APPROVAL", {"approval_id": "APR-2"})])
    loop, store, signals, ctx = world(llm, ex, state="in_transit")
    result = go(loop, ctx, (0.03, lambda: signals.signal("approval:APR-2", "denied")))
    assert result.status == "denied" and result.needs_recovery is True
    assert store.transfers["TR-1"]["state"] == "blocked"  # the sample is still in the corridor


def test_no_answer_in_time_is_a_timeout():
    llm = LLM(say(tc(1, "checkout_sample", sample_id="S")))
    ex = Executor(checkout_sample=[("PENDING_APPROVAL", {"approval_id": "APR-1"})])
    loop, store, _, ctx = world(llm, ex, timeout=0.05)
    result = go(loop, ctx)
    assert result.status == "timeout" and store.transfers["TR-1"]["state"] == "cancelled"

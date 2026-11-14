import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from agent.executor import Executor, RunContext
from agent.gate_stub import StubGate
from agent.lab import LabClient
from agent.loop import SYSTEM_PROMPT, AgentLoop
from agent.parse import Catalog, make_parse_handler
from agent.transfers import MemoryCustodyStore
from agent.verifier import ScanBus, Verifier
from services.courier.app import create_app as courier_app
from services.freezer.app import create_app as freezer_app
from services.scanner.app import create_app as scanner_app
from services.scheduler.app import create_app as scheduler_app
from services.station.app import create_app as station_app

CATALOG = Catalog({"TS-1042": "FZ-B"}, {"FZ-B": "Freezer B", "SEQ-3": "Sequencing Station 3"})


def tc(n, name, **args):
    args = {"reason": "r", **args}
    return {"id": f"call_{n}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}  # fmt: skip


def say(*calls, content=None):
    return {"role": "assistant", "content": content, "tool_calls": list(calls) or None}


class ScriptedLLM:
    def __init__(self, *messages):
        self.script, self.seen = list(messages), []

    async def chat(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


class Ledger:
    def __init__(self):
        self.entries = []

    async def append(self, entry):
        self.entries.append(entry)
        return len(self.entries)


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def world(script, timeout=2.0):
    bus, clock, ledger = ScanBus(), Clock(), Ledger()

    class BusStore:
        def insert(self, event):
            bus.on_scan(event)

    readers = {"scanner:reader-fzB": "FZ-B", "scanner:reader-seq3": "SEQ-3"}
    scanner = TestClient(scanner_app(BusStore(), readers, {"TS-1042"}, "s"))
    emit = lambda s: scanner.post("/scan", json=s).raise_for_status()  # noqa: E731
    apps = {
        "freezer": freezer_app({"TS-1042": "FZ-B"}, emit, {"FZ-B": "scanner:reader-fzB"}),
        "scheduler": scheduler_app(),
        "courier": courier_app(
            {("FZ-B", "SEQ-3"): 4}, {"FZ-B": "scanner:reader-fzB"}, emit, clock=clock
        ),
        "station": station_app(emit, {"SEQ-3": "scanner:reader-seq3"}),
    }  # fmt: skip

    async def sleep(s):
        clock.t += s

    lab = LabClient(
        **{n: httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://lab")
           for n, a in apps.items()},
        sleep=sleep, poll=1.0,
    )  # fmt: skip
    store = MemoryCustodyStore(
        {"TS-1042": {"status": "stored", "location_id": "FZ-B", "checked_out_at": None}},
        {"TR-1": {"_id": "TR-1", "state": "requested", "history": []}},
    )
    notes = []

    async def notify(args, ctx):
        notes.append(args["message"])
        return {"sent": True}

    verifier = Verifier(bus, store, ledger.append, timeout)
    internal = {"parse_request": make_parse_handler(CATALOG, None), "notify": notify}
    ex = Executor(StubGate(ledger.append), lab, ledger.append, verifier, internal=internal)
    llm = ScriptedLLM(*script)
    ctx = RunContext("agent:custody-01", "user:jlee", "GRANT-0007", "TR-1", "VR-1")
    loop = AgentLoop(llm, ex, store)
    loop.parts = {"lab": lab, "bus": bus, "ledger": ledger}
    return loop, llm, ledger, store, ctx, notes


HAPPY = [
    say(tc(1, "parse_request", sample_id="TS-1042", window="2 PM",
           **{"from": "FZ-B", "to": "SEQ-3"})),
    say(tc(2, "lookup_sample", sample_id="TS-1042"),
        tc(3, "reserve_station", station_id="SEQ-3", window="2 PM")),
    say(tc(4, "request_courier", **{"from": "FZ-B", "to": "SEQ-3"}, window="2 PM")),
    say(tc(5, "notify", user_id="user:jlee", message="Transfer booked")),
    say(tc(6, "checkout_sample", sample_id="TS-1042")),
    say(tc(7, "handoff_to_courier", transfer_id="TR-1")),
    say(tc(8, "deliver", transfer_id="TR-1")),
    say(content="TS-1042 is at Sequencing Station 3."),
]  # fmt: skip


def run(loop, text, ctx):
    return asyncio.run(loop.run(text, ctx))


def test_happy_path_every_step_gated_and_state_advances_only_through_scans():
    loop, llm, ledger, store, ctx, notes = world(HAPPY)
    result = run(loop, "Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM.", ctx)
    assert result.status == "done" and "Sequencing Station 3" in result.text
    kinds = [e["entry_type"] for e in ledger.entries]
    assert kinds.count("decision") == 8 and kinds.count("outcome") == 8
    assert [e["status"] for e in ledger.entries if e["entry_type"] == "verification"] == [
        "confirmed"
    ] * 3
    assert [h["state"] for h in store.transfers["TR-1"]["history"]] == [
        "reserved", "checked_out", "in_transit", "received",
    ]  # fmt: skip
    assert store.samples["TS-1042"]["location_id"] == "SEQ-3" and notes == ["Transfer booked"]
    # the gate's checkout preconditions read these off the transfer document
    transfer = store.transfers["TR-1"]
    assert (transfer["reservation_id"], transfer["courier_job_id"]) == ("RES-1", "CJ-1")


def test_tool_results_go_back_to_the_model_with_matching_ids():
    loop, llm, *_ = world(HAPPY)
    run(loop, "go", RunContext("agent:custody-01", "user:jlee", "GRANT-0007", "TR-1", "VR-1"))
    last = llm.seen[-1]
    tool_msgs = [m for m in last if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == [f"call_{i}" for i in range(1, 9)]
    assert json.loads(tool_msgs[0]["content"])["ok"] is True
    assert last[0]["role"] == "system" and last[1] == {"role": "user", "content": "go"}


def test_unparseable_arguments_are_reported_to_the_model_not_executed():
    bad = {
        "id": "call_x",
        "type": "function",
        "function": {"name": "lookup_sample", "arguments": "{"},
    }
    loop, llm, ledger, _, ctx, _ = world([say(bad), say(content="sorry")])
    result = run(loop, "go", ctx)
    assert result.status == "done" and ledger.entries == []
    assert "invalid" in llm.seen[1][-1]["content"].lower()


def test_halt_stops_the_loop_without_another_model_call():
    class HaltGate:
        async def check(self, **kw):
            from agent.gate_stub import GateDecision

            return GateDecision(decision="halt", policy_id="p", policy_version=1, ledger_id=1)

    loop, llm, _, _, ctx, _ = world(
        [say(tc(1, "lookup_sample", sample_id="TS-1042")), say(content="x")]
    )
    loop.executor.gate = HaltGate()
    result = run(loop, "go", ctx)
    assert result.status == "halted" and len(llm.seen) == 1


def test_runaway_model_is_cut_off():
    spin = [say(tc(i, "lookup_sample", sample_id="TS-1042")) for i in range(10)]
    loop, _, _, _, ctx, _ = world(spin)
    loop.max_steps = 3
    assert run(loop, "go", ctx).status == "max_steps"


def test_system_prompt_names_the_blocked_statuses_and_the_no_guessing_rule():
    for word in ("DENIED", "PENDING_APPROVAL", "PENDING_CONFIRMATION", "HALTED", "guess"):
        assert word in SYSTEM_PROMPT

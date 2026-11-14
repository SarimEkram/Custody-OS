"""M1/M2 checkpoint beats against the stubs: real lab services, scanner, verifier, loop and
intake; only the model (scripted), speech-to-text (fake) and the gate (stub) are stand-ins."""

import asyncio

from agent.gate_stub import GateDecision, StubGate
from agent.intake import Intake, MemoryRequests
from agent.runner import Runner
from agent.scripted import rule_parser
from agent.signals import Signals
from agent.voice import MemoryAudio, VoiceIntake
from tests.test_loop import CATALOG, HAPPY, world

SPOKEN = "Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM."


class FakeSTT:
    model_id = "scribe_v3"

    async def transcribe(self, audio, filename, keyterms):
        words = [{"text": t, "type": "word", "logprob": -0.05} for t in SPOKEN.split()]
        return {"text": SPOKEN, "words": words, "language_code": "eng",
                "language_probability": 1.0, "transcription_id": "tx-1"}  # fmt: skip


def spoken_request_world(gate=None):
    loop, llm, ledger, store, _, notes = world(HAPPY)
    store.transfers.clear()  # intake creates the transfer from the spoken request
    if gate:
        loop.executor.gate = gate(StubGate(ledger.append))
    requests, runner = MemoryRequests(), None

    async def find_grant(user_id):
        return "GRANT-0007" if user_id == "user:jlee" else None

    runner = Runner(loop, planner=None, store=store, requests=requests)
    intake = Intake(rule_parser(CATALOG), CATALOG, find_grant, requests, store, ledger.append,
                    start_run=runner.start)  # fmt: skip
    voice = VoiceIntake(FakeSTT(), MemoryAudio(), intake, CATALOG)
    return loop, voice, runner, ledger, store, requests


def test_beat_1_a_spoken_request_ends_received_with_every_step_verified_by_a_scan():
    loop, voice, runner, ledger, store, requests = spoken_request_world()

    async def main():
        out = await voice.submit("user:jlee", b"opus-bytes", "clip.webm")
        await asyncio.wait(runner.tasks)
        return out

    out = asyncio.run(main())
    assert out["transfer_id"] == "TR-1" and runner.errors == {}
    assert store.transfers["TR-1"]["state"] == "received"
    assert requests.docs["VR-1"]["source"] == "voice" and requests.docs["VR-1"]["audio_sha256"]
    first = ledger.entries[0]
    assert first["entry_type"] == "intake" and first["on_behalf_of"] == "user:jlee"
    statuses = [e["status"] for e in ledger.entries if e["entry_type"] == "verification"]
    assert statuses == ["confirmed"] * 3  # checkout, handoff, intake: all scanner-confirmed


class ApproveLater:
    """Beat 2: the forensic checkout is held for a supervisor; nothing else is blocked."""

    def __init__(self, inner):
        self.inner, self.approved = inner, False

    async def check(self, **kw):
        if kw["tool"] == "checkout_sample" and not self.approved:
            lid = await self.inner.append({"entry_type": "decision", "decision": "pending_approval",
                                           "transfer_id": kw["transfer_id"],
                                           "correlation_id": kw["correlation_id"]})  # fmt: skip
            return GateDecision(decision="pending_approval", policy_id="p", policy_version=1,
                                approval_id="APR-1", rule_hits=["forensic_requires_supervisor"],
                                ledger_id=lid)  # fmt: skip
        return await self.inner.check(**kw)


def test_beat_2_blocked_until_a_supervisor_approves_then_resumes_where_it_stopped():
    gate_holder = {}

    def make(inner):
        gate_holder["gate"] = ApproveLater(inner)
        return gate_holder["gate"]

    loop, voice, runner, ledger, store, _ = spoken_request_world(make)
    loop.signals, loop.wait_timeout = Signals(), 5.0
    bus, at_approval = loop.parts["bus"], {}

    def supervisor_approves():
        at_approval["transfer_state"] = store.transfers["TR-1"]["state"]
        at_approval["blocked_reason"] = store.transfers["TR-1"]["blocked_reason"]
        at_approval["scans"] = [s["type"] for s in bus.scans.values()]  # the freezer saw nothing
        gate_holder["gate"].approved = True
        loop.signals.signal("approval:APR-1", "approved")

    async def main():
        await voice.submit("user:jlee", b"opus-bytes", "clip.webm")
        asyncio.get_running_loop().call_later(0.3, supervisor_approves)
        await asyncio.wait(runner.tasks)

    asyncio.run(main())
    assert at_approval == {"transfer_state": "blocked", "blocked_reason": "approval_pending",
                           "scans": []}  # fmt: skip
    assert runner.errors == {} and store.transfers["TR-1"]["state"] == "received"
    decisions = [e["decision"] for e in ledger.entries if e["entry_type"] == "decision"]
    assert (
        "pending_approval" in decisions and decisions.index("pending_approval") < len(decisions) - 1
    )

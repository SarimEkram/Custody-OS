"""B's seams onto Person A's gate and custody modules (the real Atlas-backed behaviour).

AtlasExecutor keeps the interface the loop already uses (`execute(call, ctx) -> ToolResult`) but
every call goes through `policy_gate.execute`, which decides, writes the decision and outcome
ledger rows, and only then runs B's callback (the lab call). For physical steps it then waits
for the scanner's event to have been *applied* (state, sample and verification row written in
one transaction by `custody.apply_verified_scan`) before reporting `confirmed`.

ScanApplier is what the change-stream watcher calls for each custody_event: it applies the scan
exactly once and publishes the outcome for whoever is waiting on that correlation id.
"""

from collections.abc import Awaitable, Callable

from agent import custody, policy_gate
from agent.executor import RunContext, ToolCall, ToolResult
from agent.lab import LabClient
from agent.verifier import ScanBus

# The model is told to give a `reason`, but sometimes names it differently ("why" seen live with
# gpt-oss-120b). Those words mean the same thing, so they are read as the reason and removed from the
# arguments. Any OTHER unknown argument still reaches the guardrail and is refused.
REASON_ALIASES = ("reason", "why", "rationale", "justification")


def split_reason(raw: dict) -> tuple[dict, str]:
    """(arguments without any reason field, the reason text)."""
    reason = next((raw[k] for k in REASON_ALIASES if isinstance(raw.get(k), str) and raw[k].strip()), "")
    return {k: v for k, v in raw.items() if k not in REASON_ALIASES}, reason


def _value(x) -> str:
    return str(getattr(x, "value", x))


class ScanApplier:
    def __init__(self, apply: Callable[[dict], Awaitable] = custody.apply_verified_scan,
                 bus: ScanBus | None = None) -> None:  # fmt: skip
        self.apply, self.bus = apply, bus or ScanBus()

    async def handle(self, scan: dict) -> None:
        result: dict = {"state": None, "error": None}
        try:
            result["state"] = _value(await self.apply(scan))
        except Exception as e:  # noqa: BLE001 - a bad scan must not kill the watcher; verifier reports it
            result["error"] = f"{type(e).__name__}: {e}"
        self.bus.on_scan({**scan, "_result": result})


class AtlasExecutor:
    def __init__(
        self,
        lab: LabClient,
        internal: dict,
        applied: ScanBus,
        *,
        execute=policy_gate.execute,
        get_transfer=None,
        record_unverified=custody.record_unverified,
        next_state=custody.NEXT_STATE,
        timeout: float = 10.0,
        make_context=policy_gate.RunContext,
    ) -> None:
        self.lab, self.internal, self.applied = lab, internal, applied
        self.execute_fn, self.get_transfer = execute, get_transfer
        self.record_unverified, self.next_state = record_unverified, next_state
        self.timeout, self.make_context = timeout, make_context

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        args, reason = split_reason(call.args)
        gate_ctx = self.make_context(
            transfer_id=ctx.transfer_id, on_behalf_of=ctx.on_behalf_of, grant_id=ctx.grant_id,
            request_id=ctx.request_id, actor=ctx.actor,
            mode="normal" if ctx.outcome_type == "outcome" else "recovery",
        )  # fmt: skip

        async def run_it(tool: str, a: dict, correlation_id: str) -> dict:
            if tool in self.internal:
                return await self.internal[tool](a, ctx)
            return await self.lab.call(tool, a, correlation_id, ctx.refs)

        res = await self.execute_fn(call.name, args, reason, gate_ctx, run_it)
        if res.status != "OK":
            content = {"message": res.message, "rule_hits": res.rule_hits,
                       "approval_id": res.approval_id}  # fmt: skip
            return ToolResult(tool_call_id=call.id, status=res.status,
                              correlation_id=res.correlation_id, content=content)  # fmt: skip
        verification = None
        if res.expected_scan:
            verification = await self._verify(res, ctx)
        return ToolResult(
            tool_call_id=call.id, status="OK", correlation_id=res.correlation_id,
            content={"message": res.message, **(res.response or {})}, verification=verification,
        )  # fmt: skip

    async def _verify(self, res, ctx: RunContext) -> str:
        scan = await self.applied.wait_for(res.correlation_id, self.timeout)
        if scan is None:
            transfer = await self.get_transfer(ctx.transfer_id)
            await self.record_unverified(transfer, None, "unconfirmed",
                                         f"no {res.expected_scan} scan before the timeout",
                                         res.correlation_id)  # fmt: skip
            return "unconfirmed"
        landed = scan["_result"]["state"]
        want = self.next_state.get(res.expected_scan)
        return "confirmed" if landed is not None and landed == _value(want) else "mismatch"

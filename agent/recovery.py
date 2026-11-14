"""Recovery planner: what happens after a halt (grant revoked, Stop pressed, approval denied).

Reversible work is rolled back newest-first. A sample that is already out cannot be rolled
back, so it is recovered: sent to the nearest certified freezer it can still reach before its
clock runs out, with the exposure logged. Running out of time is a recorded violation and a
compromised sample, not a crash. Compensations and recovery run through execute() like every
other call, acting for the person who revoked.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from agent.executor import Executor, RunContext, ToolCall
from agent.transfers import MemoryCustodyStore


def choose_freezer(
    etas: dict[str, float],
    certs: dict[str, set[str]],
    required: set[str],
    budget_minutes: float,
) -> tuple[str | None, bool]:
    """Fastest certified freezer; the bool says whether it is reachable inside the budget."""
    ok = {f: m for f, m in etas.items() if required <= certs.get(f, set())}
    if not ok:
        return None, False
    best = min(ok, key=ok.get)
    return best, ok[best] <= budget_minutes


@dataclass
class RecoveryResult:
    status: str  # cancelled | recovered | recovered_late | unconfirmed | no_freezer
    freezer_id: str | None = None
    minutes_out: float | None = None
    exceeded: bool = False


class RecoveryPlanner:
    def __init__(
        self,
        executor: Executor,
        store: MemoryCustodyStore,
        append: Callable[[dict], Awaitable[Any]],
        read_outcomes: Callable[[str], Awaitable[list[dict]]],
        job_state: Callable[[str], Awaitable[dict]],
        freezers: dict[str, set[str]],
        bus,
        demo_speed: float = 60.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.executor, self.store, self.append = executor, store, append
        self.read_outcomes, self.job_state, self.freezers = read_outcomes, job_state, freezers
        self.bus, self.demo_speed, self.clock = bus, demo_speed, clock
        self.n = 0

    async def _call(self, ctx: RunContext, name: str, **args):
        self.n += 1
        args["reason"] = args.get("reason", f"recovery after halt of {ctx.transfer_id}")
        return await self.executor.execute(
            ToolCall(id=f"recovery-{self.n}", name=name, args=args), ctx
        )

    async def _log(self, ctx: RunContext, kind: str, summary: str, **extra) -> None:
        await self.append(
            {
                "timestamp": self.clock(),
                "entry_type": kind,
                "actor": "system:recovery",
                "on_behalf_of": ctx.on_behalf_of,
                "grant_id": ctx.grant_id,
                "policy_id": "recovery",
                "policy_version": 1,
                "decision": "allow",
                "transfer_id": ctx.transfer_id,
                "sample_id": ctx.refs.get("sample_id"),
                "request_id": ctx.request_id,
                "correlation_id": f"c-{uuid.uuid4().hex[:8]}",
                "summary": summary,
                **extra,
            }
        )

    async def run(self, ctx: RunContext, revoker: str) -> RecoveryResult:
        tid, sample_id = ctx.transfer_id, ctx.refs["sample_id"]
        if (await self.store.get_transfer(tid))["state"] not in ("revoked", "recovering"):
            await self.store.set_state(tid, "revoked")
        comp = replace(ctx, on_behalf_of=revoker, outcome_type="compensation")  # shares refs

        job_id = ctx.refs.get("courier_job_id")
        job = await self.job_state(job_id) if job_id else None
        picked_up = bool(job) and job["status"] in ("in_transit", "arrived")

        done = [
            e
            for e in await self.read_outcomes(tid)
            if e.get("reversible") is True and not (e.get("response") or {}).get("error")
        ]
        for e in reversed(done):  # newest first
            match e["tool"]:
                case "notify":
                    msg = f"Retraction: the transfer of {sample_id} was stopped and is rolled back."
                    await self._call(comp, "notify", user_id=e["args"]["user_id"], message=msg)
                case "request_courier" if not picked_up:
                    await self._call(comp, "cancel_courier_job", job_id=e["response"]["job_id"])
                case "reserve_station":
                    rid = e["response"]["reservation_id"]
                    await self._call(comp, "cancel_reservation", reservation_id=rid)

        sample = await self.store.get_sample(sample_id)
        if sample["status"] != "out":  # never left the freezer: nothing to recover
            await self.store.set_state(tid, "cancelled")
            return RecoveryResult("cancelled")

        await self.store.set_state(tid, "recovering")
        limit, out_at = sample["max_minutes_out"], sample["checked_out_at"]
        spent = (self.clock() - out_at).total_seconds() * self.demo_speed / 60
        origin = (job or {}).get("origin") or ctx.refs["from"]
        etas = {origin: 0.0}
        if picked_up:
            etas = {
                origin: job["progress"] * job["eta_minutes"],
                job["destination"]: (1 - job["progress"]) * job["eta_minutes"],
            }
        etas = {f: m for f, m in etas.items() if f in self.freezers}
        freezer, _ = choose_freezer(
            etas, self.freezers, set(sample.get("required_certifications", [])), limit - spent
        )
        if freezer is None:
            await self._log(
                ctx,
                "violation",
                f"No certified freezer reachable for {sample_id}.",
                rule_hits=["no_freezer"],
            )
            return RecoveryResult("no_freezer")

        ctx.refs["return_to"] = freezer
        res = await self._call(
            replace(comp, outcome_type="recovery"),
            "return_to_storage",
            sample_id=sample_id,
            freezer_id=freezer,
        )
        if res.status != "OK" or res.verification != "confirmed":
            await self._log(
                ctx,
                "violation",
                f"Return of {sample_id} to {freezer} was not confirmed by a scan.",
                rule_hits=["return_unconfirmed"],
            )
            return RecoveryResult("unconfirmed", freezer)

        scanned_at = self.bus.scans[res.correlation_id]["scanned_at"]
        minutes_out = (scanned_at - out_at).total_seconds() * self.demo_speed / 60
        exceeded = minutes_out > limit
        await self._log(
            ctx,
            "exposure",
            f"{sample_id} was out {minutes_out:.1f} min (limit {limit}).",
            response={"minutes_out": minutes_out, "exceeded": exceeded, "limit": limit},
        )
        if exceeded:
            await self.store.mark_compromised(sample_id)
            await self._log(
                ctx,
                "violation",
                f"{sample_id} exceeded its {limit} min limit (excursion).",
                rule_hits=["excursion"],
            )
        return RecoveryResult(
            "recovered_late" if exceeded else "recovered", freezer, minutes_out, exceeded
        )

"""Mock courier. Jobs move a sample along a route; position lives in memory only
(it goes to the dashboard over SSE, never to Atlas: 100 ops/sec budget).

Reversible until pickup (cancel). After pickup the only option is reroute.
shortcut: reroute supports "back to origin" or a known route from the current destination;
a real courier would need live position routing.
"""

import time
from collections.abc import Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from services.common import Emitter, need_correlation


class JobBody(BaseModel):
    sample_id: str
    origin: str
    destination: str
    transfer_id: str


class RerouteBody(BaseModel):
    destination: str


class Flaky(BaseModel):
    drop_scans: bool = False


def create_app(
    travel: dict[tuple[str, str], float],
    readers: dict[str, str],
    emit: Callable[[dict], None],
    clock: Callable[[], float] = time.time,
    demo_speed: float = 60.0,
) -> FastAPI:
    """travel: minutes between two locations (either order). readers: location -> reader_id."""
    app = FastAPI(title="courier")
    scanner = Emitter(emit)
    jobs: dict[str, dict] = {}

    def minutes(a: str, b: str) -> float:
        if (a, b) in travel:
            return travel[(a, b)]
        if (b, a) in travel:
            return travel[(b, a)]
        raise HTTPException(400, f"no route {a} -> {b}")

    def view(job: dict) -> dict:
        progress = 0.0
        if job["status"] in ("picked_up", "rerouted"):
            elapsed = (clock() - job["leg_started"]) * demo_speed / 60
            progress = min(1.0, elapsed / job["eta_minutes"])
        status = job["status"]
        if progress >= 1:
            status = "arrived"
        elif progress > 0 or status in ("picked_up", "rerouted"):
            status = "in_transit"
        return {**job, "status": status, "progress": progress}

    def get_or_404(jid: str) -> dict:
        if jid not in jobs:
            raise HTTPException(404, f"unknown job {jid}")
        return jobs[jid]

    @app.post("/jobs", status_code=201)
    def create_job(body: JobBody) -> dict:
        eta = minutes(body.origin, body.destination)
        jid = f"CJ-{len(jobs) + 1}"
        jobs[jid] = {"job_id": jid, **body.model_dump(), "eta_minutes": eta, "status": "assigned"}
        return view(jobs[jid])

    @app.get("/jobs")
    def list_jobs() -> list[dict]:
        return [view(job) for job in jobs.values()]

    @app.get("/jobs/{jid}")
    def get_job(jid: str) -> dict:
        return view(get_or_404(jid))

    @app.post("/jobs/{jid}/pickup")
    def pickup(jid: str, x_correlation_id: str | None = Header(None)) -> dict:
        cid = need_correlation(x_correlation_id)
        job = get_or_404(jid)
        if job["status"] != "assigned":
            raise HTTPException(409, f"{jid} is {job['status']}")
        job["status"], job["leg_started"] = "picked_up", clock()
        emitted = scanner.send(
            readers[job["origin"]],
            "handoff",
            job["sample_id"],
            job["origin"],
            cid,
            from_custodian=f"location:{job['origin']}",
            to_custodian=f"courier:{jid}",
        )
        return {**view(job), "scan_emitted": emitted}

    @app.delete("/jobs/{jid}")
    def cancel(jid: str) -> dict:
        job = get_or_404(jid)
        if job["status"] not in ("assigned", "cancelled"):
            raise HTTPException(409, f"{jid} already picked up; reroute instead")
        job["status"] = "cancelled"
        return view(job)

    @app.post("/jobs/{jid}/reroute")
    def reroute(jid: str, body: RerouteBody) -> dict:
        job = get_or_404(jid)
        if job["status"] not in ("picked_up", "rerouted"):
            raise HTTPException(409, f"{jid} has not been picked up")
        so_far = view(job)["progress"] * job["eta_minutes"]
        if body.destination == job["origin"]:
            eta = max(so_far, 0.1)
        else:
            eta = minutes(job["destination"], body.destination)
        job.update(
            destination=body.destination, eta_minutes=eta, leg_started=clock(), status="rerouted"
        )
        return view(job)

    @app.post("/admin/flaky")
    def flaky(body: Flaky) -> dict:
        scanner.drop = body.drop_scans
        return {"drop_scans": scanner.drop}

    return app

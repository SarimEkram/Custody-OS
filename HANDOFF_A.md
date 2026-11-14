# Person A handoff: what's ready and how to plug in

Everything below runs against our Atlas cluster today and is covered by tests (`pytest -q`: 44 unit + 12 Atlas integration).

## Setup (once per laptop)

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
# .env: get MONGODB_URI_AGENT / _SCANNER / _ADMIN and SCANNER_HMAC_SECRET from Person A (privately)
.venv/bin/python -m db.seed --reset      # fresh demo data (collections/indexes already exist)
```

## For B (agent + services)

**Every tool call goes through the gate:**
```python
from agent.policy_gate import RunContext, execute
ctx = RunContext(transfer_id="TR-0031", on_behalf_of="user:jlee", grant_id="GRANT-0007", request_id="VR-0019")
result = await execute(tool, args, reason, ctx, executor)   # executor(tool, args, correlation_id) -> dict
```
- `executor` is yours: map tool → lab-service HTTP call, send `X-Correlation-Id: <correlation_id>`. It is only called on `allow`.
- `result.status`: `OK` | `DENIED` | `PENDING_APPROVAL` | `PENDING_CONFIRMATION` | `HALTED` | `ERROR`. Feed `result.message` back to the LLM as the tool result. On `HALTED`, exit the loop.
- `result.expected_scan` is set for physical steps: call `await custody.wait_for_scan(result.correlation_id)` (returns the scan or `None` after `VERIFIER_TIMEOUT_SECONDS`).
- Recovery: `RunContext(..., mode="recovery", on_behalf_of=<revoker>)` lets only `cancel_reservation`, `cancel_courier_job`, `notify`, `return_to_storage` through, with no grant needed.
- Tool list, reversibility, expected scans: `agent/tools.py`. Add new tools there first.
- **Fields you set on `transfers`** after an OK result: `reservation_id`, `courier_job_id`, and `state: "reserved"` once both are done; `state: "recovering"` when recovery starts; `"cancelled"` when compensation finishes with nothing irreversible done. Never set `checked_out`/`in_transit`/`received`/`recovered` yourself; the verifier does that from scans.
- Create the `transfers` doc (state `requested`, `source` `voice`/`typed`, `requested_by`, `grant_id`, `request_id`) when a request arrives. The agent user can insert it. Write the `intake` ledger row with `ledger.append()`.

**Watchers** (start once in the agent service):
```python
from agent.watchers import Watchers
asyncio.create_task(Watchers(on_halt=start_recovery, on_approval=resume_or_cancel, on_scan=on_scan).run())
```
- `on_halt(transfer, reason, by_user)`: the transfer is already flagged `halt`, set to `revoked`, and a `halt` ledger row is written. You run compensation/recovery.
- `on_approval(approval_doc)`: `status` approved → the transfer is already unblocked, re-run the step; denied → compensate and cancel.
- `on_scan(scan)`: call `await custody.apply_verified_scan(scan)`. It checks the HMAC, sample and state, then updates `samples` + `transfers` + `ledger` in **one transaction**, and writes `exposure`/`violation` rows on check-in/intake.

**Scanner service:** sign each scan with `agent.signing.sign_scan(scan)` before inserting with `scanner_db()` (the `scanner_svc` user). Fields: `scanner_id` (`scanner:reader-fzb`…, see seed), `type`, `sample_id`, `location_id`, `correlation_id`, `scanned_at`, `from_custodian`, `to_custodian`.

**API routes ready to mount:** `app.include_router(agent.api.router)` gives confirm, approvals, revoke, stop, SSE stream and all auditor routes (list at the top of `agent/api.py`).

## For C (dashboard + demo)

- **SSE:** `GET /api/stream` emits `{"kind": "ledger"|"transfer"|"scan"|"approval", "data": {...}}` (ObjectIds/dates as strings, `embedding` stripped). `courier_position` and `confirmation_request` come from B.
- **Buttons:** `POST /api/transfers/{id}/confirm` `{user_id, readback_text, method}` · `POST /api/approvals/{id}` `{decided_by, approve}` · `POST /api/grants/{id}/revoke` `{by, reason}` · `POST /api/control/stop` `{by, transfer_id?}`. Permission errors come back as 403 with a readable message (e.g. "the requester cannot approve their own transfer").
- **Auditor:** `POST /api/audit/query {question}` → `{route, rows, routed_by}`. Canned: `/api/audit/chain/{sample}`, `/transfer/{id}`, `/gaps`, `/restricted-without-supervisor` (`{violations: [], blocked_attempts: [...]}`), `/exposure`, `/denials`, `/requests/{id}/verify`.
- **Shapes:** `agent/models.py` is the source for `dashboard/src/lib/types.ts`.
- **Seed:** `db/seed.py` has people, `GRANT-0007`, `TS-1042`/`EV-2201`/`TS-1077`, 5 locations with map coords, policies v2/v3 + recovery, 7 incidents, and an "earlier today" completed transfer plus one denial. Extend it as you like. It must pass `db/validators/`.
  - Timing note: FZ-A ↔ SEQ-3 is **2 min** so TS-1077 (3-min limit) passes the checkout rule "travel time < limit". Agree with B how `DEMO_SPEED` maps minutes to seconds.
- **Beat 4 terminal:** `mongosh` as `agent_svc`: `db.ledger.updateOne({}, {$set: {decision: "allow"}})` → *not authorized*; `db.ledger.insertOne({tool: "x"})` → *Document failed validation*.

## Decisions and findings

- Custom roles work on our cluster (layer 2 on). Automated Embedding works with `voyage-4` (not `voyage-3.5`).
- Gate approval: one supervisor approval covers all irreversible steps of that transfer. The requester can't approve their own transfer, and the approver needs the sample's clearance.
- A tech without clearance for a sample's restriction level also needs supervisor approval for irreversible steps (`requester_lacks_clearance`).
- **Risk flags are off** (`RISK_ENABLED = False` in `policy_gate.py`). Measured similarity of real actions to incidents is 0.65 to 0.73, below the 0.85 threshold, and too close between routine and risky moves to be reliable. First item on the cut list anyway.
- Groq key not set yet. `/audit/query` falls back to keyword routing, which handles the demo questions. Set `GROQ_API_KEY` to get LLM routing.

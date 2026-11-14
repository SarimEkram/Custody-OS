# Tasks

One file for the whole team. **Each person edits only their own section.** Pull before editing, push right after, so we don't step on each other.

**How to write a task**
```
- [ ] Short task description
- [ ] Task that's waiting on someone (blocked: B, needs the agent loop calling execute())
- [x] Finished task
```
- `[ ]` to do · `[x]` done
- If you're blocked, add `(blocked: B/C, why)` and also list it under **Blockers** below, so the other person sees it.
- When the blocker clears, remove it from both places.

---

## Blockers

Who is waiting on whom. Remove a line once it's resolved.

| Waiting | On | For what |
|---|---|---|
| A | B | Real agent loop calling `policy_gate.execute()`, to test the gate end to end |
| A | B | Groq API key, to test the auditor's LLM routing |
| A | B + C | Running services and dashboard, to rehearse demo beats 1–3 |
| A | C | Auditor panel calling `/api/audit/*`, to check the results look right on screen |

---

## Person A: rules and database (Sarim)

**Done**
- [x] Atlas: network access, `admin_svc` user
- [x] Custom roles `agentRole` / `scannerRole`; users `agent_svc` / `scanner_svc` (`db/roles.md`)
- [x] Cluster check: 19/19 features verified (`db/check_cluster.py` → `db/CLUSTER_CHECK.md`)
- [x] `$jsonSchema` validators for ledger, custody_events, voice_requests, transfers, grants, approvals
- [x] Collections, TTL indexes, vector indexes with Automated Embedding (`db/indexes.py`)
- [x] GridFS `voice_audio` bucket ready for the agent user
- [x] Shared models and tool catalogue (`agent/models.py`, `agent/tools.py`)
- [x] `ledger.append()` + append-only grep test
- [x] Policy gate: `check()` / `execute()`, all 7 checks, read-back rule, supervisor approvals, recovery mode
- [x] Verified scan transaction + scan signing (`agent/custody.py`, `agent/signing.py`)
- [x] Watchers: revoke, expiry, Stop, approvals, scans + SSE event feed (`agent/watchers.py`)
- [x] Auditor: custody chain, gaps, restricted-without-supervisor, exposure, meaning-based search (`agent/audit.py`)
- [x] API routes for B to mount (`agent/api.py`); revoke/stop scripts
- [x] Seed data (`db/seed.py`), handed to C to extend
- [x] Docs updated to match the real cluster; `HANDOFF_A.md`

**To do**
- [ ] Watcher also unblocks a transfer when an approval changes directly in the DB (safety net)
- [ ] Narrow `admin_svc` from Atlas admin to `readWrite` + `dbAdmin` on `custody`
- [ ] Install `mongosh`, rehearse beat 4 ("not authorized" / "failed validation")
- [ ] Test auditor LLM routing (blocked: B, needs the Groq key)
- [ ] Gate + verifier end to end with the real agent (blocked: B, needs the agent loop on `execute()`)
- [ ] Rehearse beats 1–3 with the full system (blocked: B + C)
- [ ] Check auditor results in the dashboard (blocked: C, needs the auditor panel)

**Stretch (only after the demo works)**
- [ ] Gate-signed token that lab services verify
- [ ] Save change-stream resume tokens in a `stream_state` collection

---

## Person B: agent and fake lab

*B: replace or adjust these; they're copied from `BUILD_PLAN.md`.*

- [ ] Scaffold mock services: freezer, scheduler, courier, station (ports 8101–8104)
- [ ] Scanner service (8105): sign scans with `agent.signing.sign_scan`, insert as `scanner_svc`
- [ ] `scripts/stt_smoke.py`: one ElevenLabs test call
- [ ] `scripts/llm_smoke.py`: one Groq tool-call test
- [ ] Typed request path `POST /transfers` (creates transfer + `intake` ledger row)
- [ ] Agent loop: every tool call through `policy_gate.execute()`
- [ ] Executor: tool → lab service HTTP call with `X-Correlation-Id`
- [ ] Mount `agent.api.router`; start `Watchers` on startup
- [ ] Verifier: `on_scan` → `custody.apply_verified_scan()`
- [ ] Voice intake `POST /voice/requests`: ElevenLabs STT, GridFS, `voice_requests`
- [ ] `parse_request` with DB validation and "did you mean" for low confidence
- [ ] `request_confirmation` tool + read-back text
- [ ] Handle `PENDING_APPROVAL` (wait) and `HALTED` (exit loop)
- [ ] Recovery planner: compensate reversible steps, `return_to_storage` for irreversible
- [ ] `DEMO_SCRIPTED=1` replay mode

---

## Person C: dashboard and demo

*C: replace or adjust these; they're copied from `BUILD_PLAN.md`.*

- [ ] Vite + React + TS + Tailwind scaffold, layout, Vite proxy `/api` → 8000
- [ ] `src/lib/types.ts` from `agent/models.py`; `src/lib/api.ts`
- [ ] SSE client (`/api/stream`) + store
- [ ] Lab floor map with moving sample dot
- [ ] Live ledger stream with SENT → CONFIRMED badges
- [ ] Push-to-talk mic + typed fallback box
- [ ] Read-back confirm card
- [ ] Approval inbox + identity switcher (Jordan Lee / M. Okafor)
- [ ] Stop button, Revoke button, countdown
- [ ] Auditor panel: query box, timeline with gaps, audio play
- [ ] Extend `db/seed.py` (more history, voice requests)
- [ ] `scripts/reset_demo.sh` (< 15 s)
- [ ] Backup screen recording
- [ ] Slides and pitch

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
| A | B | Real agent loop calling `policy_gate.execute()`, to test the gate end to end. **Built on B's branch; waiting on B to merge it to `main`** |
| A | B | Groq API key, to test the auditor's LLM routing. **B has it in B's `.env`; B sends it to A privately (never through git)** |
| A | B + C | Running services and dashboard, to rehearse demo beats 1–3 |
| A | C | Auditor panel calling `/api/audit/*`, to check the results look right on screen |
| B | A + C | Agree a time for the first live end-to-end run (it needs `python -m db.seed --reset`, which resets everyone's data) |
| B | C | Agree `DEMO_SPEED` (seed `location_id` typos are fixed by A: B can drop the workaround in `services/config.py`) |

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
- [x] Watcher also unblocks a transfer when an approval changes directly in the DB (safety net)
- [x] Seed bug: scanner `location_id`s fixed in `db/seed.py` and in the live data (`FZ-A`, `SEQ-3`, ...)
- [x] API accepts C's dashboard request bodies and returns the dashboard's audit fields (`agent/api.py`)
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

**Status:** all of B's list is built and tested on branch `person-b/agent-and-services` (264 unit tests; live checks passed: Groq tool call, ElevenLabs transcript, Atlas stores / transaction / change stream, and the real agent service boots against Atlas). **It is not on `main` yet**, and it has **not been run end to end with A's real gate on Atlas**. Run instructions and the contracts for A and C are in `HANDOFF_B.md` (on the branch).

**Done**
- [x] Mock services: freezer, scheduler, courier, station (ports 8101–8104); they start from `.lab_config.json`, exported from the seeded Atlas data (`scripts/export_lab_config.py`), so they hold no DB credentials
- [x] Scanner service (8105): signs scans with `agent.signing.sign_scan`, inserts as `scanner_svc`
- [x] `scripts/stt_smoke.py`: one ElevenLabs test call (PASS, `scribe_v3`)
- [x] `scripts/llm_smoke.py`: one Groq tool-call test (PASS, `openai/gpt-oss-120b`)
- [x] Typed request path `POST /transfers` (creates transfer + `intake` ledger row)
- [x] Agent loop: every tool call through `policy_gate.execute()` (`agent/atlas.py`, `agent/loop.py`)
- [x] Executor: tool → lab service HTTP call with `X-Correlation-Id` (`agent/lab.py`)
- [x] Mount `agent.api.router`; start `Watchers` on startup (`agent/main.py`)
- [x] Verifier: `on_scan` → `custody.apply_verified_scan()` (`ScanApplier`; the loop waits for the applied result)
- [x] Voice intake `POST /voice/requests`: ElevenLabs STT, GridFS, `voice_requests`
- [x] `parse_request` with DB validation and "did you mean" for low confidence
- [x] `request_confirmation` tool + read-back text (generated by code from the validated transfer)
- [x] Handle `PENDING_APPROVAL` (wait, then retry) and `HALTED` (exit loop; the run is cancelled even mid-tool)
- [x] Recovery planner: compensate reversible steps, `return_to_storage` for irreversible
- [x] `DEMO_SCRIPTED=1` replay mode (the mechanism; the script itself is still to be recorded, see below)
- [x] Courier position and `confirmation_request` events on `/api/stream` (merged with A's feed)
- [x] Atlas-backed stores (transfers, samples, voice requests, GridFS audio, grants, catalog) and `HANDOFF_B.md`

**To do**
- [ ] Merge `person-b/agent-and-services` into `main` (agent/, services/, scripts/, `HANDOFF_B.md`)
- [ ] First live end-to-end run of beats 1–3 with A's gate on Atlas (needs `python -m db.seed --reset`; agree a time first)
- [ ] Record `demo/script.json` from a good live run, so `DEMO_SCRIPTED=1` has something to replay
- [ ] Agree `DEMO_SPEED` with C (FZ-A ↔ SEQ-3 is 2 min; TS-1077's limit is 3 min)
- [ ] Rehearse beats 1–3 with the full system (blocked: A + C)

**Stretch (only after the demo works)**
- [ ] Spoken read-backs with ElevenLabs text-to-speech
- [ ] Crash-resume of an interrupted transfer; resume token for the scan feed

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

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
| A | B | Groq API key, to test the auditor's LLM routing. **B has it in B's `.env`; B sends it to A privately (never through git)** |
| A | B + C | Running services and dashboard, to rehearse demo beats 1–3 |
| A | C | Auditor panel calling `/api/audit/*`, to check the results look right on screen |
| B | A + C | Agree a time for the first live end-to-end run (it needs `python -m db.seed --reset`, which resets everyone's data) |
| B | C | Agree `DEMO_SPEED`, and point the dashboard at the real agent (it currently runs against `mock-data/server.mjs`). Seed `location_id` typos are fixed by A: B can drop the workaround in `services/config.py` |

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
- [x] Narrow `admin_svc` from Atlas admin to `readWrite` + `dbAdmin` on `custody`
- [x] Install `mongosh`, rehearse beat 4 (`db/BEAT4_CHEATSHEET.md`)
- [ ] Test auditor LLM routing (blocked: B, needs the Groq key)
- [ ] Gate + verifier end to end with the real agent (B's agent loop is on `main` now; needs a live run)
- [ ] Rehearse beats 1–3 with the full system (blocked: B + C)
- [ ] Check auditor results in the dashboard (blocked: C, needs the auditor panel)

**Stretch (only after the demo works)**
- [ ] Gate-signed token that lab services verify
- [ ] Save change-stream resume tokens in a `stream_state` collection

---

## Person B: agent and fake lab

**Status:** all of B's list is built and tested (264 unit tests; live checks passed: Groq tool call, ElevenLabs transcript, Atlas stores / transaction / change stream, and the real agent service boots against Atlas). **Merged to `main`** (PR #1), along with C's dashboard (`3ef3450`). It has **not been run end to end with A's real gate on Atlas**, and the dashboard has not been run against the real agent yet. Run instructions and the contracts for A and C are in `HANDOFF_B.md`.

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
- [x] Merge `person-b/agent-and-services` into `main` (PR #1: agent/, services/, scripts/, `HANDOFF_B.md`)
- [x] Seed fix: scanner readers now point at real location ids (was `FZA`, `SEQ3`…)

**To do**
- [ ] Merge `origin/main` (C's dashboard) into `feature/tts-spoken-prompts`, then open the PR for it
- [ ] First live end-to-end run of beats 1–3 with A's gate on Atlas (needs `python -m db.seed --reset`; agree a time first)
- [ ] Record `demo/script.json` from a good live run, so `DEMO_SCRIPTED=1` has something to replay
- [ ] Agree `DEMO_SPEED` with C (FZ-A ↔ SEQ-3 is 2 min; TS-1077's limit is 3 min)
- [ ] Rehearse beats 1–3 with the full system (blocked: A + C)

**Spoken prompts (branch `feature/tts-spoken-prompts`)**

Done (backend; 281 unit tests pass, ruff clean; live check `scripts/tts_smoke.py` PASS with the real key, voice id and `eleven_flash_v2_5`):
- [x] `ElevenLabsTTS` in `agent/voice.py`; built in `agent/main.py` only when `ELEVENLABS_API_KEY` and `ELEVENLABS_TTS_VOICE_ID` are set
- [x] `SpokenPrompts`: id-only, server-built text (stored read-back, or a fixed approval template), cached by text hash
- [x] `GET /transfers/{id}/readback/audio` and `GET /approvals/{id}/audio` (`audio/mpeg`; 409 nothing pending, 404 unknown approval, 503 on TTS failure; routes absent when TTS is off)
- [x] `confirmation_request` event now carries `readback_text` (what the dashboard reads; it ignored `text`) and `audio_url` when TTS is on
- [x] Contract for C written up in `HANDOFF_B.md` ("Spoken prompts"); docs and `.env.example` updated

Still to do:
- [ ] **C:** dashboard playback: speak on `confirmation_request` (`audio_url`) and on a pending approval (`/api/approvals/{id}/audio`), replay button, mute toggle. Lands in `ReadbackCard` / `ApprovalInbox`; details in `HANDOFF_B.md`
- [ ] Walk demo beat 1 with the full system, audio included (needs A + C and the live end-to-end run above)
- [ ] Optional: ledger entry when a prompt is spoken (what the tech heard), and a cap on the audio cache

**Stretch (only after the demo works)**
- [ ] Gate-signed token that lab services verify
- [ ] Save change-stream resume tokens in a `stream_state` collection

---

## Person B: agent and fake lab

**Status:** all of B's list is built and tested (264 unit tests; live checks passed: Groq tool call, ElevenLabs transcript, Atlas stores / transaction / change stream, and the real agent service boots against Atlas). **Merged to `main`** (PR #1), along with C's dashboard (`3ef3450`). It has **not been run end to end with A's real gate on Atlas**, and the dashboard has not been run against the real agent yet. Run instructions and the contracts for A and C are in `HANDOFF_B.md`.

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
- [x] Merge `person-b/agent-and-services` into `main` (PR #1: agent/, services/, scripts/, `HANDOFF_B.md`)
- [x] Seed fix: scanner readers now point at real location ids (was `FZA`, `SEQ3`…)

**To do**
- [ ] Merge `origin/main` (C's dashboard) into `feature/tts-spoken-prompts`, then open the PR for it
- [ ] First live end-to-end run of beats 1–3 with A's gate on Atlas (needs `python -m db.seed --reset`; agree a time first)
- [ ] Record `demo/script.json` from a good live run, so `DEMO_SCRIPTED=1` has something to replay
- [ ] Agree `DEMO_SPEED` with C (FZ-A ↔ SEQ-3 is 2 min; TS-1077's limit is 3 min)
- [ ] Rehearse beats 1–3 with the full system (blocked: A + C)

**Spoken prompts (branch `feature/tts-spoken-prompts`)**

Done (backend, 279 unit tests pass, ruff clean; TTS is unit-tested against a mock transport only):
- [x] `ElevenLabsTTS` in `agent/voice.py`; built in `agent/main.py` only when `ELEVENLABS_API_KEY` and `ELEVENLABS_TTS_VOICE_ID` are set
- [x] `SpokenPrompts`: id-only, server-built text (stored read-back, or a fixed approval template), cached by text hash
- [x] `GET /transfers/{id}/readback/audio` and `GET /approvals/{id}/audio` (`audio/mpeg`; 409 nothing pending, 404 unknown approval, 503 on TTS failure; routes absent when TTS is off)
- [x] Docs and `.env.example` updated

Still to do:
- [ ] Live check with the real key and voice id (the voice id is now in `.env`): start the agent, trigger a read-back, `GET` the audio route and play the MP3. Not yet confirmed that the key's TTS permission works.
- [ ] Walk demo beat 1 after `scripts/reset_demo.sh` (the audio routes are additive, but this hasn't been run)
- [ ] Dashboard playback (play on `confirmation_request` / pending approval, replay button, mute toggle): the frontend is on `main` now, so this is unblocked: it lands in C's `ReadbackCard` (`dashboard/src/components/command/`) and `ApprovalInbox` (`dashboard/src/components/approvals/`). Agree with C who writes it. Nothing started.
- [ ] Optional: ledger entry when a prompt is spoken (what the tech heard), and a cap on the audio cache

**Stretch (only after the demo works)**
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

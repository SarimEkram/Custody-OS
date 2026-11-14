# Build plan (one day, team of 3)

Rule: **the demo path must work end to end as early as possible, then get richer.** Don't start a later milestone while an earlier one is broken.

## Team split

| Person | Owns | Done means |
|---|---|---|
| **A: Data & policy** | Atlas cluster, DB users and custom roles, `$jsonSchema` validators (incl. `voice_requests`), GridFS `voice_audio` bucket, gate read-back rule (`pending_confirmation`), indexes (TTL + vector), the **policy gate**, change stream listeners (grants, approvals, control, custody_events), auditor Vector Search, aggregation pipelines | Gate denies/allows correctly with unit tests; validators and roles reject bad writes; watchers set halt/resume flags; canned aggregations return correct rows |
| **B: Services & agent** | Mock FastAPI services (`freezer`, `scheduler`, `courier`, `station`), the **scanner**, the Claude agent loop (`claude-opus-5-5`, manual tool-use loop), tool executor, verifier (sent → confirmed), recovery planner, **voice intake backend** (`POST /voice/requests`, ElevenLabs STT call with keyterms, GridFS + `voice_requests` write, `parse_request` with DB validation and low-confidence handling, read-back text) | A spoken request drives real HTTP calls through the gate; scans advance state; revoke triggers compensation + recovery |
| **C: Dashboard & demo** | React + TypeScript dashboard (Vite) (map, live ledger, approvals, Stop, countdown, auditor panel), **push-to-talk mic UI** (MediaRecorder, live transcript display, typed fallback box, read-back confirm card, optional TTS playback, audio play button in the auditor), SSE client, **seed data**, demo script and reset script, backup recording, pitch/slides | Dashboard shows every beat in `docs/DEMO.md` live; reset runs in < 15 s; recording and slides ready |

Pairing points: A+B on `execute()` (gate ↔ executor), the verifier transaction and the read-back rule; B+C on the `/voice/requests` contract (multipart fields, response with transcript + parsed fields + low-confidence terms); B+C on the SSE event shape and courier position ticks; A+C on auditor query API and seed data matching the validators.

## Hour 1: shared contracts (all three, together, ~45 min)

Agree on and commit these before splitting up. Put them in code, not just docs.

1. **Document shapes**: Pydantic models in `agent/models.py` for every collection in `docs/DATA_MODEL.md`; TypeScript mirror in `dashboard/src/lib/types.ts`. Validators JSON in `db/validators/`.
2. **Transfer states**: `requested`, `reserved`, `checked_out`, `in_transit`, `received`, plus `blocked`, `revoked`, `recovering` (and terminal `recovered`, `cancelled`). Transitions as in `docs/ARCHITECTURE.md`. One `Enum` in Python, one union type in TS.
3. **Gate signature**: `check(...) -> GateDecision` and `execute(tool_call, ctx) -> ToolResult` exactly as in `docs/ARCHITECTURE.md`. B can stub it as "always allow" until A delivers.
4. **Tool catalogue**: names, args, reversible flag, expected scan type.
5. **Service ports and endpoints**: agent 8000, freezer 8101, scheduler 8102, courier 8103, station 8104, scanner 8105, dashboard 5173 (Vite dev server, proxies `/api` → 8000).
6. **SSE event shape**: `{ "kind": "ledger" | "transfer" | "scan" | "approval" | "confirmation_request" | "courier_position", "data": {...} }`.
6b. **Voice contract**: `POST /voice/requests` (multipart `audio` + `user_id`) → `{request_id, transcript, parsed, low_confidence_terms, transfer_id}`; `POST /transfers` (typed) returns the same shape; `POST /transfers/{id}/confirm`.
7. **Env vars**: `.env.example` with all keys (incl. `ELEVENLABS_API_KEY`); Atlas URIs for the three DB users.

Also in hour 1: A creates the DB users on the Atlas Sandbox, shares the URIs, and runs `db/check_sandbox.py` (see DATA_MODEL.md) so everyone knows what works; B scaffolds the five FastAPI apps and makes one test call to ElevenLabs STT with a recorded clip (`scripts/stt_smoke.py`) to confirm the key, model id and keyterms work; C scaffolds the React + TypeScript app with Vite (`react-ts` template + Tailwind), the layout, and a hard-coded fake ledger.

## Milestones and checkpoints

### M1: happy path end to end (target: **by noon**)
- [A] Validators + indexes created by `db/indexes.py`; `agent_svc` / `scanner_svc` / `admin_svc` users exist (custom roles if the sandbox allows); `agent/ledger.py` with `append()` only, plus the no-update grep test.
- [A] Gate v1: grant valid + scope + halt flag. Writes decision entries.
- [A] Change stream on `custody_events` → callback hook B can register.
- [B] Four mock services with real state; each emits raw scans to the scanner.
- [B] Scanner writes signed `custody_events` with its own user.
- [B] Typed request path first (`POST /transfers`), so the agent isn't blocked on audio.
- [B] Agent loop: one request → `parse_request`, lookup, reserve, courier, notify, checkout, handoff, deliver, all via `execute()`.
- [B] Verifier + handoff transaction: state advances only on scans.
- [C] Seed script (`TS-1042`, `GRANT-0007`, locations, people). Dashboard: map with moving dot, live ledger via SSE, transfer state.
- [B] Voice intake: `/voice/requests` → ElevenLabs `scribe_v3` → `voice_requests` + GridFS → same agent path.
- [C] Push-to-talk button + typed fallback box, transcript shown as soon as it returns.
- **Checkpoint:** Beat 1 runs from a **spoken** request on the dashboard, start to `received`, with SENT → CONFIRMED badges (typed fallback also works).

### M2: blocked and revoked (target: **mid-afternoon, ~3 PM**)
- [A] Gate read-back rule: voice-originated transfers need a `confirmation` entry before irreversible tools.
- [B] `request_confirmation` tool + read-back text; low-confidence / unknown-ID handling ("Did you mean TS-1042 or TS-1024?").
- [C] Read-back confirm card (button, plus say "confirm").
- [A] Gate v2: restriction level (`forensic_requires_supervisor` → `pending_approval` + approval doc), certification, checkout preconditions.
- [A] Watchers: `grants` (revoke/delete → halt), `approvals` (→ resume), `control` (Stop).
- [A] TTL indexes on grants/reservations/approvals. Revoke script.
- [B] Agent handles `PENDING_APPROVAL` (pause and wait) and `HALTED` (exit loop) tool results.
- [B] Recovery planner: compensate reversible in reverse order; return-to-storage for irreversible; exposure / violation entries.
- [C] Approval inbox with identity switcher; Stop button; countdown; Revoke button on grant card; seed `EV-2201`, `TS-1077`, policy versions.
- **Checkpoint:** Beats 2 and 3 run cleanly three times in a row after `reset_demo.sh`.

### M3: auditor and proof (target: ~4:30 PM)
- [A] Canned aggregations: custody chain, gaps, restricted-without-supervisor, exposure. Auditor API endpoint.
- [A] Ledger `summary` + embeddings on insert; `ledger_vector` index; NL query → `{semantic_query, filters}` → `$vectorSearch`.
- [B] Seed ~30 historical ledger entries and incidents (with C); `DEMO_SCRIPTED=1` replay mode.
- [C] Auditor panel: query box, results, timeline with gaps highlighted, original request transcript with audio play button. mongosh "not authorized" / "failed validation" moment rehearsed.
- **Checkpoint:** full demo (beats 0–4) under 4 minutes.

### Feature freeze: **late afternoon (~5 PM)**
After freeze: bug fixes, rehearsal, recording, slides only. No new features.
- [C] Record the backup video of the full demo.
- [All] Three full rehearsals with reset between each. Assign who talks for each beat.

## Stretch (only after M3 checkpoint, in this order)
1. Vector Search **risk flagging** in the gate (`incidents_vector`). *This is the first thing to cut if time is short.*
1b. ElevenLabs **text-to-speech** read-backs (`eleven_flash_v2_5`): the agent speaks the confirmation question.
1c. Realtime transcription (`scribe_v3_live` WebSocket) so words appear while the tech is talking.
2. Seal IDs on scans + `seal_check` mismatch violation.
3. Gate-signed short-lived token that lab services verify (defense in depth: services reject calls that didn't pass the gate).
4. Crash/resume demo: kill the agent mid-transfer, restart, it resumes from verified state.
5. Resume tokens persisted in a `stream_state` collection.

## Cut list (if behind schedule, cut in this order)
1. Risk flagging via Vector Search (keep Vector Search in the auditor).
2. NL auditor → keep canned aggregation buttons only.
2b. Voice confirmation ("say confirm") → button only. Keep spoken requests themselves unless ElevenLabs is down, then demo on the typed fallback.
3. Courier animation → step the dot between locations.
4. Custom roles → built-in roles + app-level `ledger.append()`-only enforcement (keep the validator demo).
5. Separate service processes → one FastAPI app with routers per "system" (keep the scanner separate no matter what; independence is core to the story).

Never cut: the typed fallback, the pre-action gate, the scanner-only `custody_events`, append-only ledger, the revoke → recovery beat.

## Definition of done for any task
- Works from a fresh `scripts/reset_demo.sh`.
- Every new tool has gate rules, a reversible flag, a compensation/recovery path and its expected scan.
- Every new ledger write passes the validator (no relaxing it to make things work).
- Demo beat 1 still passes.

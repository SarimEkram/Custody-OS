# Architecture

## Components

```
┌──────────────────────────────────────────────────────────────────────┐
│ React + TypeScript dashboard (Vite, :5173)                           │
│  🎙 push-to-talk request · lab floor map · live ledger · approval    │
│  inbox · read-back confirm · Stop · countdown · auditor search       │
└───────────────▲───────────────────────────────┬──────────────────────┘
                │ SSE (relayed change streams)  │ REST (voice audio, typed requests,
                │                               │ confirmations, approvals, stop)
┌───────────────┴───────────────────────────────▼──────────────────────┐
│ Agent service  (:8000)  Python · FastAPI · Anthropic SDK             │
│                                                                      │
│  Voice intake: audio → ElevenLabs STT (scribe_v3) → transcript ─────────► api.elevenlabs.io
│     → voice_requests doc + audio in GridFS → transfer request        │
│        │                                                             │
│        ▼                                                             │
│  Agent loop (claude-opus-5-5, tool use)                              │
│        │ every tool_use block                                        │
│        ▼                                                             │
│  Policy Gate ──► ledger: decision entry (BEFORE the call)            │
│        │ allowed only                                                │
│        ▼                                                             │
│  Tool executor ──► HTTP to lab service ──► ledger: outcome entry     │
│                                                                      │
│  Verifier: waits for matching custody_event (sent → confirmed)       │
│  Watchers: change streams on grants, approvals, custody_events,      │
│            control (stop switch)                                     │
│  Recovery planner: compensate reversible / recover irreversible      │
└───────┬───────────────┬───────────────┬───────────────┬──────────────┘
        ▼               ▼               ▼               ▼
   freezer :8101   scheduler :8102  courier :8103   station :8104     (mock lab systems)
        │                               │               │
        └──── emit physical "happenings" (checkout, pickup, dropoff, intake)
                                │
                                ▼
                   Scanner service :8105  (independent verifier)
                   own DB user, ONLY writer of custody_events
                                │
                                ▼
                         MongoDB Atlas
```

### Agent service (`/agent`)
- **Voice intake (`agent/voice.py`):** receives recorded audio from the dashboard, calls ElevenLabs speech-to-text, stores audio + transcript + speaker identity, and starts a transfer from the transcript. See "Voice intake" below.
- **Agent loop:** manual Anthropic tool-use loop with `claude-opus-5-5`. System prompt describes the lab, the rules, and that tool results may be `DENIED`, `PENDING_APPROVAL` or `HALTED`. The model must give a `reason` argument on every tool call; it goes into the ledger.
- **Policy gate:** pure function plus DB reads. See contract below.
- **Tool executor:** maps tool names to lab-service HTTP calls. Passes `correlation_id` in a header (`X-Correlation-Id`).
- **Verifier:** after a physical action, waits (change stream on `custody_events`, filtered by `correlation_id`) up to a timeout for the scanner's confirmation. Returns `confirmed`, `mismatch` or `unconfirmed`.
- **Watchers:** background tasks holding change streams; set a per-transfer `halt` flag the loop checks before every step.
- **Recovery planner:** builds and runs the compensation/recovery plan on halt.
- **API for dashboard:** `POST /voice/requests` (multipart audio upload, the main path), `POST /transfers` (typed request, the fallback), `POST /transfers/{id}/confirm` (read-back confirmation), `POST /approvals/{id}` (approve/deny), `POST /control/stop`, `POST /grants/{id}/revoke`, `GET /stream` (SSE), `POST /audit/query`.

### Voice intake (ElevenLabs speech-to-text)

A lab tech holds a push-to-talk button and says the request, e.g. *"Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM."* The agent works from the transcript.

**Flow**
1. **Record (dashboard):** `MediaRecorder` captures `audio/webm` (Opus) while the button is held. Browsers only allow the mic on `https` or `localhost`, so run the dashboard on `localhost` for the demo.
2. **Upload:** dashboard `POST /voice/requests` (multipart: `audio`, plus the logged-in `user_id` from the session/identity switcher). The ElevenLabs API key stays on the server; the browser never sees it.
3. **Transcribe (agent service):** `POST https://api.elevenlabs.io/v1/speech-to-text`, header `xi-api-key`, multipart fields:
   - `file`: the audio
   - `model_id`: `scribe_v3` (current batch model; `scribe_v2` is the previous one if v3 misbehaves)
   - `language_code`: `en` (skip auto-detection for speed and accuracy)
   - `keyterms`: sample IDs and location names from the DB (`TS-1042`, `EV-2201`, `Freezer B`, `Sequencing Station 3`, …) so lab vocabulary is recognized. Keyterms add a ~20% surcharge; fine for a demo.
   - `timestamps_granularity`: `word`
   
   The response has `text`, `language_code`, `language_probability` and `words[]` (each with `text`, `start`, `end`, `type`, `logprob`). Batch (not realtime) is the right fit: requests are one sentence, and batch is simpler to make reliable. Realtime (`scribe_v3_live` over `wss://api.elevenlabs.io/v1/speech-to-text/realtime`) is a stretch only.
4. **Store:** audio goes to GridFS bucket `voice_audio` with its SHA-256 hash; a `voice_requests` doc stores the transcript, words with confidence, model id, `audio_sha256`, and the **logged-in speaker identity**. Then an `intake` ledger entry links the request to the new transfer. See `DATA_MODEL.md`.
5. **Parse:** the agent loop gets the transcript as the user message. Its first step is a `parse_request` tool call (structured output: `sample_id`, `from`, `to`, `window`) which the code checks against the DB. If a sample ID or location doesn't exist, or the words for it have low confidence (`logprob` below a threshold), the agent asks the tech to repeat or pick from close matches instead of guessing.
6. **Proceed** through the normal gate, exactly like a typed request.

**Rules**
- **The voice grants no authority.** Who is speaking is the logged-in identity in the dashboard, not the voice. We don't do voice biometrics. A spoken request passes through exactly the same gate, grant and approval checks as a typed one. (A supervisor approval can't be given by voice in the demo; it's a button click as the supervisor identity.)
- **Read-back before irreversible steps.** For a voice-originated transfer, before the first irreversible tool (`checkout_sample`), the agent produces a read-back: *"I'll check out TS-1042 from Freezer B and send it to Sequencing Station 3, arriving about 2:04. Confirm?"* The tech confirms by button (or by saying "confirm", transcribed the same way). The gate enforces this: no confirmation means `pending_confirmation`, and the transfer goes to `blocked` with `blocked_reason: "readback_pending"`. The confirmation is a ledger entry.
- **Spoken confirmations (optional):** play the read-back aloud with ElevenLabs text-to-speech (`eleven_flash_v2_5` or `eleven_v4_turbo` for low latency). This is a nice touch for the demo, not a dependency; the text read-back is always shown.
- **Typed fallback:** the same request box accepts typed text. It creates a `voice_requests`-shaped doc with `source: "typed"` and no audio, so everything downstream is identical. Use it if the mic, network or ElevenLabs fails.
- **Audit value:** every ledger entry carries `request_id`, so an auditor can go from any action back to the original audio, the exact transcript and the speaker, and see what the agent read back and who confirmed it.

### Mock lab systems (`/services/*`)
Small FastAPI apps, each with its own in-memory or own-collection state, so the agent's actions are real API calls to separate systems.

| Service | Endpoints (examples) | Notes |
|---|---|---|
| `freezer` | `POST /checkout`, `POST /checkin`, `GET /inventory` | Checkout sets `checked_out_at`. Can be told to fail/flake for demos. |
| `scheduler` | `POST /reservations`, `DELETE /reservations/{id}` | Reservations have TTL. |
| `courier` | `POST /jobs`, `DELETE /jobs/{id}`, `POST /jobs/{id}/reroute` | Simulates travel along a route (ticks position every second). |
| `station` | `POST /intake`, `GET /status` | Receiving station; also used for "return to freezer" via freezer `checkin`. |

When a physical thing happens (sample leaves freezer, courier picks up, courier arrives, station receives), the service calls the **scanner** with a raw scan (`POST /scan`), simulating a barcode/RFID reader at that location. The lab service does **not** write to MongoDB `custody_events` itself.

### Scanner service (`/services/scanner`)
- Receives raw scans from "readers" (the lab services and a manual `scripts/scan.py` for demos).
- Validates the scan (known reader, known sample, plausible location) and writes `custody_events` using **its own DB user** (`scanner_writer`). The agent's DB user can only *read* `custody_events`.
- This is the independence story: the agent can claim anything in the ledger, but only scans count as proof, and the agent can't forge them.

### Dashboard (`/dashboard`)
React + TypeScript SPA built with Vite. One page, a few components (`LabMap`, `LedgerStream`, `MicButton`, `ReadbackCard`, `ApprovalInbox`, `Countdown`, `AuditorPanel`). A `useEventStream()` hook wraps `EventSource('/api/stream')` and dispatches events into a small reducer (or Zustand store). Vite's dev proxy sends `/api/*` to the agent service on :8000.
- **Lab floor map:** SVG of locations (Freezer A/B, Corridor, Sequencing Station 3, Forensics Lab, etc.). Sample dot moves using courier position events.
- **Live ledger:** streaming table; colour by decision (`allow` green, `deny` red, `pending` amber, `halt` grey). Each row shows *sent* vs *confirmed* badge.
- **Approval inbox:** pending approvals with Approve/Deny (writes to `approvals`).
- **Stop button** and **countdown** (from `samples.checked_out_at` + `max_minutes_out`).
- **Auditor panel:** natural-language query box + custody-chain timeline per sample with gaps highlighted.

## Shared contracts (agree in the first hour)

### Transfer state machine

```
requested ──► reserved ──► checked_out ──► in_transit ──► received   (happy path)
    │             │              │               │
    └──► blocked (awaiting approval) ──► (approved) back to previous step / (denied) cancelled
                  │              │               │
           revoked/stopped at any step ──► revoked ──► recovering ──► recovered | cancelled
```

| State | Meaning | Entered when |
|---|---|---|
| `requested` | Request parsed, nothing done yet | `POST /transfers` |
| `reserved` | Station reserved + courier assigned (both reversible) | both confirmed by service responses |
| `checked_out` | Sample out of freezer, clock running | **scanner** confirms freezer checkout |
| `in_transit` | Courier has the sample | **scanner** confirms courier pickup handoff |
| `received` | Destination has it; transfer closed | **scanner** confirms station intake |
| `blocked` | Gate returned `pending_approval` or `pending_confirmation` (`blocked_reason`: `approval_pending` / `readback_pending`) | gate decision |
| `revoked` | Grant revoked/expired or Stop pressed | watcher sets halt flag |
| `recovering` | Running recovery plan | recovery planner starts |
| `recovered` | Sample back in certified storage, exposure logged | scanner confirms freezer check-in |
| `cancelled` | Ended with nothing irreversible done, or approval denied | compensation complete |

State changes for `checked_out`, `in_transit`, `received`, `recovered` are only made by the verifier after a scan, inside a transaction (see `DATA_MODEL.md`).

### Tool catalogue

| Tool | Lab system | Reversible? | Compensation / recovery | Expected scan |
|---|---|---|---|---|
| `parse_request(sample_id, from, to, window)` | (internal, validated against DB) | read-only | — | — |
| `request_confirmation(summary)` | (internal; shows read-back, optional TTS) | read-only | — | — |
| `lookup_sample(sample_id)` | freezer | read-only | — | — |
| `reserve_station(station_id, window)` | scheduler | reversible | `cancel_reservation` | — |
| `request_courier(from, to, window)` | courier | reversible | `cancel_courier_job` | — |
| `notify(user_id, message)` | (internal) | reversible-ish | send retraction notice | — |
| `checkout_sample(sample_id)` | freezer | **irreversible** | recovery: return to nearest certified freezer | `checkout` at freezer |
| `handoff_to_courier(transfer_id)` | courier | **irreversible** | recovery: reroute courier | `handoff` freezer→courier |
| `deliver(transfer_id)` | station | **irreversible** | recovery: reroute | `intake` at station |
| `return_to_storage(sample_id, freezer_id)` | courier+freezer | recovery action | — | `checkin` at freezer |

Every tool takes a required `reason: str`. Irreversible tools have stricter gate rules (see below).

### Gate function signature

```python
class GateDecision(BaseModel):
    decision: Literal["allow", "deny", "pending_approval", "pending_confirmation", "halt"]
    policy_id: str
    policy_version: int
    rule_hits: list[str]          # e.g. ["forensic_requires_supervisor"]
    risk_flags: list[str] = []    # from Vector Search similar incidents
    approval_id: str | None = None
    ledger_id: ObjectId           # the decision entry already written

async def check(
    *,
    actor: str,                   # "agent:custody-01"
    on_behalf_of: str,            # "user:jlee"
    grant_id: str,
    transfer_id: str,
    tool: str,
    args: dict,
    reason: str,
    correlation_id: str,
) -> GateDecision: ...

async def execute(tool_call: ToolCall, ctx: RunContext) -> ToolResult:
    """check() → if allow: call lab service → write outcome entry → verify if physical."""
```

### Gate checks (in order; first failure wins)
1. **Halt flag** set for this transfer → `halt`.
1b. **Read-back** (voice-originated transfers, irreversible tools only): no `confirmation` entry for this transfer → `pending_confirmation`.
2. **Grant valid:** exists, `status == "active"`, `expires_at > now`, belongs to `on_behalf_of`, delegated to this agent.
3. **Scope:** tool in `grant.scope.actions`, sample type and locations in scope, time window OK.
4. **Restriction level:** `forensic` + irreversible tool → needs an approved `approvals` doc for this transfer, else `pending_approval` (creates approval request).
5. **Certification:** destination has the certification the sample needs.
6. **Preconditions for irreversible tools:** e.g. `checkout_sample` requires station reserved **and** courier assigned **and** route ETA < `max_minutes_out`.
7. **Risk (Vector Search, cuttable):** find similar past incidents; if similarity > threshold, add `risk_flags` and require approval.

Write order: decision entry (with `decision`) → (if allow) call service → outcome entry referencing the decision `_id`.

## Data flow: happy path

1. Tech holds push-to-talk and says "Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM." ElevenLabs returns the transcript; agent stores the audio, transcript and speaker → `voice_requests` doc, `transfers` doc `requested`, `intake` ledger entry.
2. Agent calls `parse_request`, then `lookup_sample`, `reserve_station`, `request_courier`, `notify`. Each passes the gate; each gets decision + outcome entries. State → `reserved`.
3. Agent shows (and optionally speaks) the read-back; tech confirms → `confirmation` ledger entry. Agent calls `checkout_sample`. Gate checks the confirmation and the preconditions. Freezer service performs it and sends a raw scan to scanner. Ledger outcome says `status: "sent"`.
4. Scanner writes `custody_events{type: "checkout", correlation_id}`. Verifier sees it via change stream, runs the handoff transaction: `samples.location`, `transfers.state = checked_out`, ledger `verification: confirmed`. Countdown starts on the dashboard.
5. Same pattern for courier pickup (`in_transit`) and station intake (`received`).

## Stop and recovery (rollback vs. recovery)

**Triggers:** `grants` change stream sees `status: "revoked"` (or the TTL deletes it, which also shows as a delete event), `control` collection gets a stop document, or a grant's `expires_at` passes.

**Halt semantics:** the watcher sets the halt flag. The agent finishes no new tool calls: the next `check()` returns `halt`. An in-flight HTTP call is allowed to complete (we can't un-send it), and its outcome is recorded. Then the loop exits and the recovery planner runs.

**Recovery planner:**
1. Read the transfer's ledger entries in order and classify completed actions.
2. **Reversible, completed** → run compensations in reverse order (cancel courier job, cancel reservation, send retraction notice). Each compensation is itself logged with `decision: "allow"` under the **system recovery policy** (`policy_id: "recovery"`), `on_behalf_of` = the revoker.
3. **Irreversible, completed** (sample is out) → transfer state `recovering`; pick the **nearest certified freezer** reachable before `checked_out_at + max_minutes_out` (simple distance table in `locations`); call `return_to_storage`. Recovery actions are allowed even without a valid grant, but only from the `recovery` policy's allowlist.
4. When the scanner confirms `checkin`, state → `recovered`; write an `exposure` ledger entry with total minutes out and whether the limit was exceeded.
5. If the clock runs out first, write a `violation` entry (`excursion`) and flag the sample `compromised`. This is a valid outcome to show, not a crash.

Key line for the pitch: **"Reservations get rolled back. A sample in a corridor can't be, so it gets recovered, and the recovery is audited too."**

## Sent vs. confirmed

Every outcome entry has `status: "sent" | "confirmed" | "mismatch" | "unconfirmed"`.
- `sent`: the lab service accepted the call.
- `confirmed`: a scanner event with the same `correlation_id`, sample and expected location arrived within the timeout.
- `mismatch`: a scan arrived but disagrees (wrong location or sample). Raise a violation.
- `unconfirmed`: no scan before timeout. Agent may retry *only that step* (idempotent via `correlation_id`) or escalate.

The gap-finding aggregation (DATA_MODEL.md) finds every `sent` with no matching scan.

## Crash/restart (light touch)

On agent start, query `transfers` not in a terminal state and resume from `state` (the state is only advanced on verified scans, so it's trustworthy). Not a demo focus, but cheap to support and worth one sentence in the pitch.

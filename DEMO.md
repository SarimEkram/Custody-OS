# Demo script

Target length: **4 minutes** of demo inside a ~5-minute slot. Every beat must work from a clean `scripts/reset_demo.sh`.

## Screen layout

One browser window, dashboard full screen:
- **Left (60%)**: lab floor map (FZ-A, FZ-B, CORR-1 corridor, SEQ-3, FOR-LAB) with sample dots and the courier.
- **Top bar**: big push-to-talk mic button, transcript line, typed fallback box, identity switcher (Jordan Lee / M. Okafor).
- **Right (40%)**: live ledger stream on top, read-back card, approval inbox + countdown below.
- **Tabs**: Auditor view; a terminal with `mongosh` logged in as `agent_svc` (for the "can't edit the ledger" moment).

## Seed data (`db/seed.py --reset`)

| Thing | Values |
|---|---|
| People | `user:jlee` (lab tech, standard+controlled), `user:mokafor` (supervisor, all clearances) |
| Agent | `agent:custody-01` |
| Grant | `GRANT-0007`: jlee → agent, scope covers tissue + evidence, all demo locations, expires in 6 h |
| Samples | `TS-1042` tissue, `standard`, FZ-B, `max_minutes_out: 20`; `EV-2201` DNA swab, `forensic`, FZ-B, needs `FORENSIC`, case 24-1187; `TS-1077` tissue, `controlled`, FZ-A, `max_minutes_out: 3` (**short clock for the revoke beat**) |
| Locations | FZ-A, FZ-B (freezers, certified `BSL-2`, `FORENSIC`), CORR-1, SEQ-3 (`BSL-2`), FOR-LAB (`FORENSIC`) with `travel_minutes` |
| Policies | `custody-default@3` active, `custody-default@2` inactive (shows versioning), `recovery@1` |
| Incidents | 6–8 realistic past incidents with embeddings (for risk flags and auditor search) |
| History | ~30 pre-made ledger entries from "earlier today", including one past denial, so the auditor view isn't empty |

**Demo time compression:** courier travel runs at `DEMO_SPEED` (e.g. 1 real second = 1 simulated minute). The countdown shows real seconds. `TS-1077`'s short limit makes the revoke beat land in ~60–90 s.

## Beats

### Beat 0: the hook (20 s, slide or voice-over)
"Chain of custody decides if evidence holds up in court. We built an agent that moves evidence and can't act on its own say-so."

### Beat 1: normal run (60 s)
1. Hold the mic button and **say**: *"Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM."* The transcript appears within a second or two; the ledger's first row is the `intake` entry linking the audio, transcript and speaker (Jordan Lee, from the login, not the voice).
2. Ledger fills: decision → outcome pairs for lookup, reserve, courier, notify (green).
3. Read-back card: *"I'll check out TS-1042 from Freezer B and send it to Sequencing Station 3, arriving about 2:04. Confirm?"* (spoken aloud if TTS is in). Click Confirm. Say: *"Nothing irreversible happens until a human confirms the plan."*
4. `checkout_sample`: the row shows **SENT** badge, then flips to **CONFIRMED** when the scanner event arrives. Say: *"The agent's word isn't proof. Only the scanner's signed event is, and the agent's database user can't write those."*
5. Dot moves along the corridor; countdown ticks; handoff and intake confirm. Transfer `received`.

### Beat 2: blocked, then approved (50 s)
1. Say: *"Move EV-2201 to the Forensics Lab now."* Point out: *the request is spoken by a lab tech, but the voice gives no authority.* Confirm the read-back.
2. Reserve/courier allowed; `checkout_sample` → **DENIED: pending approval** (red/amber row, rule `forensic_requires_supervisor`). Point out: *the freezer service never received the call*, show its request log is empty.
3. Approval appears in the inbox. Switch identity to M. Okafor (dropdown) and click Approve.
4. Change stream fires, agent resumes exactly where it stopped. Checkout proceeds and confirms.

### Beat 3: revoked mid-transfer (70 s) ← the showstopper
1. Request (spoken, or typed to save time): *"Move TS-1077 from Freezer A to Sequencing Station 3."* Confirm the read-back.
2. Once it is `in_transit` (dot in the corridor, 3-minute clock running), run `python scripts/revoke_grant.py GRANT-0007` **or** click Revoke on the grant card.
3. Ledger: `halt` row (grant revoked by jlee). Agent stops. Transfer → `revoked` → `recovering`.
4. Compensations appear: reservation cancelled, notification retracted (*"those we can roll back"*).
5. Recovery: courier rerouted to the nearest certified freezer (FZ-A); countdown keeps running; check-in scan confirms with seconds to spare. `exposure` entry: *"2 m 41 s out, within limit."*
6. Say: *"You can't undo a physical move. So reversible actions get rolled back, irreversible ones get recovered, and the recovery is audited too."*

### Beat 4: the auditor (40 s)
1. Auditor tab. Ask: *"Show every time a restricted sample was moved without a supervisor."* → canned aggregation returns **zero rows**, plus the denied attempt from beat 2 shown as "blocked".
2. Ask: *"What happened to TS-1077?"* → timeline, starting from the original spoken request (click play to hear it; hash verified), then requests, decisions with policy version, scans, halt, compensations, recovery, exposure.
3. Terminal as `agent_svc`: `db.ledger.updateOne({}, {$set: {decision: "allow"}})` → **not authorized**. Then `db.ledger.insertOne({tool: "x"})` → **Document failed validation**.

### Close (15 s)
MongoDB features shown: GridFS (original audio), validation, custom roles, change streams, transactions, TTL, Vector Search, aggregations. One-line impact + "next: real RFID readers and LIMS integration".

## Voice demo safety

- **Mic:** use a headset or clip-on mic, not the laptop mic; venues are loud. Push-to-talk (hold to record), never always-listening.
- **Run on `localhost`:** browsers block the mic on plain `http` for non-localhost hosts. Grant mic permission in the browser before going on stage.
- **Keyterms** include every seeded sample ID and location name, so "TS-1042" and "Sequencing Station 3" come out right. Rehearse the exact phrasings below at least 5 times each and check the transcripts.
- **Pre-recorded clips:** keep `demo_audio/beat1.webm` etc. and an "Upload clip" option that sends them through the same `/voice/requests` path. This is real STT on known audio, a better fallback than typing if the room is noisy.
- **Typed fallback** is always visible. If ElevenLabs errors or is slow (> 5 s timeout), the UI says so and focuses the text box; the rest of the demo is identical.
- **Show the safety net once (optional, 10 s):** say a wrong ID like "TS-1024" on purpose; the agent answers "I can't find TS-1024. Did you mean TS-1042?" Only include if rehearsed.

## Demo safety

- **`scripts/reset_demo.sh`**: drops demo-mutable collections (`transfers`, `ledger` non-seed rows, `custody_events`, `approvals`, `reservations`, `control`), reseeds, restarts services, resets courier positions. Must run in < 15 s. Rehearse with it at least 3 times.
- **Seed voice history:** a few past `voice_requests` (typed source is fine) so the auditor has requests to link to.
- **Deterministic agent:** temperature 0 (or default), tight system prompt, and a fixed request phrasing per beat. If the model goes off-script, the gate still holds; but rehearse to know what it does.
- **Pre-warm:** run one throwaway request before going on stage (warms Atlas connections, embedding client, model).
- **Fallbacks:**
  - A **backup screen recording** of the full demo (record by late afternoon).
  - `DEMO_SCRIPTED=1` mode: replays a recorded tool-call sequence through the real gate and services, no model call. Use if the API is slow or the network is bad.
  - Hotspot/phone tether ready; Atlas IP access list includes `0.0.0.0/0` for the demo day only (or the venue IP).
- **Timeouts:** verifier timeout ~10 s in demo mode; show `unconfirmed` gracefully, never a stack trace.
- **Hide noise:** dashboard hides `embedding` fields and raw HTTP bodies by default (click to expand).
- **Big fonts**, high-contrast colours, no dark-on-dark. Test on the venue projector if possible.

## Judges' likely questions (have answers ready)

- *What if the agent lies in the ledger?* It can only insert, never edit; and progress is driven by scanner events it can't write. The gap query catches any "sent" without a scan.
- *What stops the agent from bypassing the gate?* The agent process only has tool definitions that route through `execute()`; lab services also check a gate-signed token (stretch) and the DB user can't edit grants.
- *Why not just use blockchain?* Atlas gives append-only via roles + validation, queryable audit, and live change streams without the overhead.
- *What if someone imitates a tech's voice?* Nothing changes: voice is only input. Authority comes from the logged-in identity's grant, sensitive moves need a supervisor's approval, and irreversible steps need a confirmation. We don't use voice for identity.
- *What if the transcript is wrong?* Parsed IDs and locations are validated against the database, low-confidence words trigger a "did you mean" question, and the read-back is the last check before anything irreversible.
- *Is the AI needed?* It plans multi-system moves under time constraints and explains its reasons, which go into the record; the gate makes it safe.

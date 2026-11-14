# CustodyOS: an AI agent you can audit

CustodyOS is an AI agent that moves lab samples and forensic evidence between locations. A lab tech **speaks** the
request. The agent plans and runs the move. Every step is tied to an identity, checked against policy **before** it
runs, independently verified, and written to a ledger that cannot be edited, so a reviewer can reconstruct exactly what
happened, when, and under whose authority.

Built in one day for the **MongoDB "Agents in Context" Toronto Hackathon**, **Track 1: Agents You Can Audit**.

> Chain of custody decides whether a DNA swab holds up in court. Today it is clipboards and signatures. Labs want
> automation, but an AI agent moving evidence is exactly the "who allowed this?" problem.

## The problem, and how we answer it

Track 1 asks for an agent that takes real actions across systems, where every action is tied to an identity, checked
against policy before it runs, and recorded so a reviewer can reconstruct it afterward.

| The question | Our answer |
|---|---|
| **How do you decide what an agent may do, and enforce it before the action?** | A **policy gate** wraps every tool call. It checks the grant (valid, in scope, not expired or revoked), the sample's restriction level, the destination's certification, the time window, and similar past incidents. A denied call never reaches a lab system. Forensic samples need a supervisor's approval. |
| **How do you record what the agent did, why, and under whose authority?** | An **append-only ledger** in MongoDB. Each entry records the actor, the human it acted for, the grant, the policy version, the agent's stated reason, the request and response, and the independent verification result. Spoken requests keep their original audio (hashed), so every entry traces back to the words that started it. |
| **How do you stop or roll back an agent mid-run?** | A **Stop** switch and a **change stream** on grants halt the agent at its next step. Reversible actions are compensated (reservation cancelled, notification retracted). A sample already out of the freezer cannot be un-moved, so a **recovery plan** returns it to the nearest certified freezer before its exposure clock runs out, and that recovery is audited too. |

## The six rules the system never breaks

1. **The agent never calls a lab system directly.** Every tool call goes through the policy gate.
2. **Decide before acting.** The gate writes its decision to the ledger *before* the action runs, then the outcome after.
3. **The agent's word isn't proof.** Only the scanner service writes `custody_events`. A step is done when a matching scan
   exists, not when the agent says it happened ("sent" is not "confirmed").
4. **The ledger is append-only.** One `append()` function uses `insert_one`. Never update, replace or delete.
   Corrections are new entries that reference the old one.
5. **Irreversible actions get stricter checks and recovery, not rollback.** Each tool is tagged reversible or irreversible.
6. **The voice grants no authority.** Identity comes from the logged-in user, never the audio. A spoken request goes through
   exactly the same gate as a typed one, and voice-originated transfers need a read-back confirmation before the first
   irreversible step.

## What you see (the dashboard)

A single-page React app with two views:

- **Control Center.** A live lab-floor map (freezers, corridor, sequencing station, forensics lab) with the sample and
  courier moving along their route. Above it, a push-to-talk mic button, the transcript, and a typed fallback. Alongside
  the map: a **read-back card** (the agent reads the plan aloud and listens for "confirm", or you press the button), a live
  **ledger stream** with SENT vs CONFIRMED badges, an **approval inbox**, an **exposure clock** that counts up while a
  sample is out of the freezer, and **Stop** and **Revoke grant** controls.
- **Auditor.** Plain-language questions ("find custody gaps", "what happened to TS-1077?") answered from the ledger, a
  custody-chain timeline from the original spoken request through decisions, scans, halts, compensations and recovery,
  playback of the original audio with a hash re-check, and an exposure report.

It also explains itself. Denials show the rule that fired in plain language and what to do next. A run that ends early
(budget, model failure) is shown as a failure, distinct from a policy decision. When a supervisor re-issues a revoked
grant, the new grant appears immediately.

## How it runs

```
 Browser (React dashboard) ──REST + SSE──▶ Agent service (FastAPI)
                                              │  policy gate ─▶ ledger ─▶ MongoDB Atlas
                                              │  voice intake (ElevenLabs STT/TTS) · LLM (Groq, tool use)
                                              ▼
              Lab systems: freezer · scheduler · courier · station      Scanner (independent verifier,
              (mock services, each with its own state)                   separate DB user, signs events)
```

- **Agent service** (`agent/`): FastAPI, a manual tool-use loop so every call can be intercepted, the policy gate, the
  recovery planner, voice intake, and guardrails (tool-call validation, argument binding, duplicate-step protection,
  fail-closed behaviour, run budgets).
- **Lab systems** (`services/`): four small mock FastAPI apps, plus the **scanner**, the only writer of `custody_events`.
- **Database** (`db/`): schema validators, indexes, roles, and idempotent seed data.
- **Dashboard** (`dashboard/`): React 19, TypeScript, Vite, Tailwind. It only talks to the agent's REST API and its
  Server-Sent Events stream. It never touches MongoDB or ElevenLabs directly.

## How MongoDB is used

MongoDB Atlas (M10 replica set) is the identity, policy and audit layer:

- **Collections and `$jsonSchema` validators** reject any ledger entry missing actor, grant, policy version or correlation ID.
- **Custom roles** give the agent, scanner and admin separate identities. Only the scanner writes `custody_events`; the
  agent cannot touch grants or rewrite history.
- **Change streams** drive the live dashboard and halt the agent the moment a grant is revoked.
- **Transactions** make grant re-issue atomic: the new grant, the replaced marker and the ledger entry succeed together or not at all.
- **GridFS** stores each spoken request's original audio with a SHA-256 hash recorded at intake.
- **Atlas Vector Search with Automated Embedding** (`voyage-4`) lets auditors search ledger and incident summaries in plain language.
- **Aggregation pipelines** build custody chains, exposure totals and gap reports.

See `DATA_MODEL.md` for schemas and `db/CLUSTER_CHECK.md` for the cluster feature check.

## Run it

### Prerequisites

Python 3.12+, Node 20+, Chrome (for the microphone), and a `.env` (copy `.env.example`) with the three MongoDB URIs
(`MONGODB_URI_AGENT`, `_SCANNER`, `_ADMIN`), `GROQ_API_KEY`, `ELEVENLABS_API_KEY`, `SCANNER_HMAC_SECRET` and the service
ports/URLs. Never commit `.env`.

### Option A: the dashboard with a mock backend (no database, no keys)

Good for rehearsing the flow. Everything is simulated and nothing touches MongoDB.

```bash
node mock-data/server.mjs               # mock agent service on :8000
cd dashboard && npm install && npm run dev   # http://localhost:5173
```

### Option B: the real stack against Atlas

```bash
# 1. Python environment
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt

# 2. One-time database setup (admin user). Seeding resets demo data on the cluster.
.venv/bin/python -m db.indexes          # collections, validators, indexes
.venv/bin/python -m db.seed --reset     # demo data (wipes transfers, ledger, scans)

# 3. Lab config for the lab services (read-only, as the scanner user)
.venv/bin/python scripts/export_lab_config.py

# 4. Start the services (export the variables from .env into the shell first)
uvicorn --factory services.freezer.main:app_factory   --port 8101 &
uvicorn --factory services.scheduler.main:app_factory --port 8102 &
uvicorn --factory services.courier.main:app_factory   --port 8103 &
uvicorn --factory services.station.main:app_factory   --port 8104 &
uvicorn --factory services.scanner.main:app_factory   --port 8105 &
uvicorn --factory agent.main:create_app               --port 8000 &

# 5. The dashboard
cd dashboard && npm install && npm run dev   # http://localhost:5173
```

The lab services read `SCANNER_URL` from the process environment and do not load `.env` themselves, so export it first
(the agent loads `.env` on its own). In dev, Vite proxies `/api` to `http://localhost:8000`, so there is no CORS setup.

### Tests

```bash
.venv/bin/python -m pytest -q -m "not integration"   # backend unit tests, no network
.venv/bin/python -m pytest -q                        # including Atlas integration tests
cd dashboard && npm test                             # dashboard unit tests
cd dashboard && npm run build && npm run lint        # type-check, build, lint
```

## The demo, in four beats

1. **Normal run.** Say "Move TS-1042 from Freezer B to Sequencing Station 3." The agent reads the plan back; you say
   "confirm". The checkout shows SENT, then flips to CONFIRMED when the scanner's signed event arrives.
2. **Blocked, then approved.** A forensic sample is denied with `forensic_requires_supervisor`. The lab service never
   receives the call. A supervisor approves, a change stream wakes the agent, and it resumes where it stopped.
3. **Revoked mid-transfer.** Revoke the grant while the courier is moving. The agent halts, reversible steps are rolled
   back, and the courier is rerouted to the nearest certified freezer before the exposure clock expires.
4. **The auditor.** Ask what happened to a sample and get the whole chain, starting from the original recording. The
   `agent_svc` database user is refused when it tries to edit the ledger.

The full script, seed data and fallbacks are in `DEMO.md`.

## Repository layout

```
agent/        agent loop, policy gate, guardrails, recovery planner, voice intake, audit API
services/     freezer, scheduler, courier, station (mock lab systems) and scanner (independent verifier)
db/           validators, indexes, roles, seed data, cluster check
dashboard/    React + TypeScript app (see dashboard/README.md)
mock-data/    zero-dependency mock backend for rehearsing the dashboard
scripts/      smoke tests and demo helpers (revoke grant, stop, export lab config)
tests/        backend tests
```

## Status and known limits

- Built in one day as a hackathon project. The lab systems are mocks; real RFID readers and LIMS integration are next.
- Speech-to-text and text-to-speech use ElevenLabs, and the agent uses a Groq-hosted model. The free tiers are rate
  limited, so `DEMO_SCRIPTED=1` keeps a scripted path ready for the demo.
- The typed fallback always works, and so does the Confirm button, if the microphone or speech service is unavailable.
- Open dashboard/backend items are tracked in `DASHBOARD_BACKEND_ISSUES.md`.

## Further reading

`HACKATHON.md` (track brief and judging), `ARCHITECTURE.md` (components, state machine, gate checks), `DATA_MODEL.md`
(schemas and indexes), `DEMO.md` (script), `GUARDRAILS_SPEC.md` (agent guardrails), `dashboard/README.md` (dashboard
status and behaviour), `TEAM.md` and `BUILD_PLAN.md`.

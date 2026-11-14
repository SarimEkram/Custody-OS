# CLAUDE.md — Custody Agent

An auditable AI agent that moves lab samples and forensic evidence between locations. A lab tech **speaks** the request (ElevenLabs speech-to-text turns it into text); the agent plans and runs the move. Every action is tied to an identity, checked against policy **before** it runs, independently verified, and written to a tamper-resistant ledger in MongoDB Atlas.

Built in one day for the **MongoDB "Agents in Context" Toronto Hackathon**, **Track 1: Agents You Can Audit**.

## Read these first

| When you are… | Read |
|---|---|
| Unsure what we're building or why | `HACKATHON.md`, `TEAM.md` |
| Touching any service or the agent loop | `ARCHITECTURE.md` |
| Touching MongoDB (schemas, indexes, queries, roles) | `DATA_MODEL.md` |
| Preparing or protecting the demo | `DEMO.md` |
| Picking the next task | `BUILD_PLAN.md` |

## The six rules (never break these)

1. **The agent never calls a lab system directly.** Every tool call goes through `policy_gate.execute()`. A denied call must never reach a lab service.
2. **Decide before acting.** The gate writes its decision to `ledger` *before* the action runs, then writes the outcome after. No action without a prior `decision` entry.
3. **The agent's word isn't proof.** Only the scanner service writes `custody_events`. A step counts as done when a matching scan exists, not when the agent says it happened (`sent` ≠ `confirmed`).
4. **The ledger is append-only.** Only `agent/ledger.py` writes it, through one `append()` function that uses `insert_one`. Never `update`, `replace` or `delete` in `ledger` (a test enforces this). Corrections are new entries that reference the old one.
5. **Irreversible actions get stricter checks and recovery, not rollback.** Each tool is tagged `reversible` or `irreversible` (see `ARCHITECTURE.md`). On a stop, undo the reversible ones and run the recovery plan for the irreversible ones.
6. **The voice grants no authority.** Identity comes from the logged-in user, never from the audio. A spoken request goes through exactly the same gate as a typed one, and voice-originated transfers need a read-back confirmation before the first irreversible step.

## Stack

- **Agent service:** Python 3.12, FastAPI, an OpenAI-compatible client (`openai` SDK) pointed at Groq's free OpenAI-compatible API (`LLM_BASE_URL`, `LLM_MODEL`, default `openai/gpt-oss-120b`) with tool use. Manual tool-use loop (we need to intercept every call). The free tier is rate-limited (about 30 req/min, 8k tokens/min), so keep the tool list and history small and keep `DEMO_SCRIPTED=1` ready. The model is swappable through the env vars; nothing else in the design depends on it.
- **Mock lab systems:** separate FastAPI apps: `freezer`, `scheduler`, `courier`, `station`. Each keeps its own state.
- **Scanner service:** FastAPI. The only writer of `custody_events`. Uses its own DB user.
- **Database:** our **MongoDB Atlas M10** cluster (`cluster0.fmdhjr`, MongoDB 9.0.7, replica set). Features: collections, GridFS, `$jsonSchema` validation, custom DB roles, change streams, transactions, TTL indexes, Atlas Vector Search with Automated Embedding, aggregation pipelines. **All verified 19/19 in `db/CLUSTER_CHECK.md`**; custom roles are live (`db/roles.md`). The free-tier limits in `DATA_MODEL.md` don't apply to M10, but keep the habits (one client per process, no per-second writes). Driver: `pymongo` (async API, `AsyncMongoClient`) in Python; the dashboard reads through the agent API, not directly.
- **Voice:** ElevenLabs speech-to-text, batch endpoint `POST https://api.elevenlabs.io/v1/speech-to-text`, `model_id=scribe_v3`, `language_code=en`, `keyterms` = sample IDs + location names. Called server-side only (key never reaches the browser). Optional ElevenLabs text-to-speech for spoken read-backs (`eleven_flash_v2_5`). Audio is stored in GridFS (`voice_audio`), transcript in `voice_requests`. Always keep the typed fallback working.
- **Embeddings:** `EMBEDDINGS_PROVIDER=atlas_auto` (verified): Atlas Automated Embedding embeds each ledger/incident `summary` itself with `voyage-4` (`AUTO_EMBED_MODEL`), so the app never calls a model and stores no `embedding` field; queries pass text (`$vectorSearch` with `query: {text}`, see `agent/embeddings.py`). Fallback `voyage`: we call `https://ai.mongodb.com/v1/embeddings` (`voyage-3.5`, 1024 dims, key from Atlas → AI Models → Model API Keys) and store `embedding` arrays.
- **Dashboard:** React 18 + TypeScript single-page app built with **Vite** (`npm create vite@latest dashboard -- --template react-ts`), Tailwind for styling. No server-side rendering: the app only talks to the agent service's REST API and its Server-Sent Events stream (`EventSource`), which relays change streams. In dev, the Vite proxy forwards `/api` to `http://localhost:8000`, so there's no CORS setup. Data fetching with plain `fetch` (or TanStack Query if it helps); lab map as inline SVG components.

## Repo layout (target)

```
/agent          # agent loop, tools, policy gate, recovery planner, voice.py (STT intake)
/services
  /freezer  /scheduler  /courier  /station   # mock lab systems
  /scanner                                   # independent verifier
/db
  validators/   # one JSON file per collection ($jsonSchema)
  check_cluster.py  # verifies sandbox features, writes CLUSTER_CHECK.md
  indexes.py    # creates all indexes incl. TTL + vector search
  roles.md      # Atlas custom roles to create in the UI/CLI
  seed.py       # demo seed data (idempotent; --reset flag)
/dashboard      # React + TypeScript (Vite) app: src/components, src/lib/api.ts, src/lib/types.ts
/scripts        # demo helpers: revoke_grant.py, reset_demo.sh, run_all.sh
/docs
```

## Conventions

- **IDs:** human-readable string `_id`s for domain objects (`TS-1042`, `GRANT-0007`, `TR-0031`); `ObjectId` for ledger and event rows.
- **Time:** store UTC `datetime` (BSON Date), never strings. The out-of-freezer clock is computed from `samples.checked_out_at`.
- **Policy versions:** policies are immutable documents with `version`; the gate records `policy_id` + `version` on every decision.
- **Every ledger write** includes `actor`, `on_behalf_of`, `grant_id`, `policy_id`, `decision`, `timestamp`, `transfer_id`, `correlation_id`, and `request_id` (the `voice_requests` doc it traces back to). The validator rejects writes without them.
- **Correlation IDs:** each tool call gets a `correlation_id`; the lab service echoes it, and the scanner includes it in its event so the agent can match `sent` to `confirmed`.
- **Config:** all settings via env vars (`.env`, never committed). `MONGODB_URI_AGENT`, `MONGODB_URI_SCANNER`, `MONGODB_URI_ADMIN`, `GROQ_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `EMBEDDINGS_PROVIDER`, `VOYAGE_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_STT_MODEL` (default `scribe_v3`), service ports.
- **Python:** type hints, Pydantic v2 models for every API body and document, `ruff` for lint/format. Keep services small: one file each is fine.
- **TypeScript:** strict mode; function components and hooks only; shared types in `dashboard/src/lib/types.ts` mirroring the Pydantic models; all API calls go through `dashboard/src/lib/api.ts`. The dashboard never connects to MongoDB or ElevenLabs directly.

## How to work in this repo

- This is a one-day hackathon build. **Demo first, polish later.** Follow the milestone order in `BUILD_PLAN.md`; don't start a "stretch" item while an earlier milestone is broken.
- Keep the demo path working at all times. Before finishing a task, run `scripts/reset_demo.sh` and walk demo beat 1 (see `DEMO.md`).
- Prefer simple, visible behavior over clever abstractions: judges score what they can see.
- Never let the agent guess a sample ID or location from a shaky transcript. Validate parsed fields against the DB and ask the tech to repeat or choose when confidence is low.
- When you add a tool for the agent, also add: its gate rules, its `reversible`/`irreversible` tag, its compensating action or recovery step, and its expected scanner event (if any).
- Don't mock out MongoDB features we claim to use. If something fails on the cluster, record it in `db/CLUSTER_CHECK.md` and `DATA_MODEL.md` and tell the team.
- Use the shared modules, don't reinvent them: `agent/db.py` (one `AsyncMongoClient` per identity), `agent/ledger.append()`, `agent/policy_gate.execute()`, `agent/custody.py` (verified scans), `agent/watchers.py`, `agent/signing.py` (scanner HMAC). No per-second writes (courier positions stay in memory and go out over SSE).

## Commands (fill in as they exist)

```bash
# setup (Python 3.12 venv)
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
# one-time (admin_svc)
.venv/bin/python -m db.check_cluster    # verify cluster features → db/CLUSTER_CHECK.md (~2 min)
.venv/bin/python -m db.indexes          # collections + validators + indexes (TTL, vector)
.venv/bin/python -m db.seed --reset     # load demo data (wipes transfers/ledger/scans)
# tests
.venv/bin/python -m pytest -q                    # all (unit + Atlas integration)
.venv/bin/python -m pytest -q -m "not integration"  # unit only, no DB
# run everything
scripts/run_all.sh            # starts lab services, scanner, agent, dashboard
# demo helpers
python scripts/revoke_grant.py GRANT-0007   # or POST /api/grants/GRANT-0007/revoke
python scripts/stop.py [--transfer TR-0031]
scripts/reset_demo.sh
```

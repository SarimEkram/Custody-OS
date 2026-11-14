# CLAUDE.md — Custody Agent

An auditable AI agent that moves lab samples and forensic evidence between locations. Every action is tied to an identity, checked against policy **before** it runs, independently verified, and written to a tamper-resistant ledger in MongoDB Atlas.

Built in one day for the **MongoDB "Agents in Context" Toronto Hackathon**, **Track 1: Agents You Can Audit**.

## Read these first

| When you are… | Read |
|---|---|
| Unsure what we're building or why | `docs/HACKATHON.md`, `docs/BACKGROUND.md` |
| Touching any service or the agent loop | `docs/ARCHITECTURE.md` |
| Touching MongoDB (schemas, indexes, queries, roles) | `docs/DATA_MODEL.md` |
| Preparing or protecting the demo | `docs/DEMO.md` |
| Picking the next task | `docs/BUILD_PLAN.md` |

## The five rules (never break these)

1. **The agent never calls a lab system directly.** Every tool call goes through `policy_gate.execute()`. A denied call must never reach a lab service.
2. **Decide before acting.** The gate writes its decision to `ledger` *before* the action runs, then writes the outcome after. No action without a prior `decision` entry.
3. **The agent's word isn't proof.** Only the scanner service writes `custody_events`. A step counts as done when a matching scan exists, not when the agent says it happened (`sent` ≠ `confirmed`).
4. **The ledger is append-only.** Only `agent/ledger.py` writes it, through one `append()` function that uses `insert_one`. Never `update`, `replace` or `delete` in `ledger` (a test enforces this). Corrections are new entries that reference the old one.
5. **Irreversible actions get stricter checks and recovery, not rollback.** Each tool is tagged `reversible` or `irreversible` (see `docs/ARCHITECTURE.md`). On a stop, undo the reversible ones and run the recovery plan for the irreversible ones.

## Stack

- **Agent service:** Python 3.12, FastAPI, Anthropic SDK, model `claude-opus-5-5` with tool use. Manual tool-use loop (we need to intercept every call).
- **Mock lab systems:** separate FastAPI apps: `freezer`, `scheduler`, `courier`, `station`. Each keeps its own state.
- **Scanner service:** FastAPI. The only writer of `custody_events`. Uses its own DB user.
- **Database:** the hackathon's **MongoDB Atlas Sandbox (Free cluster, MongoDB 8.0)**. Features: collections, `$jsonSchema` validation, DB roles, change streams, transactions, TTL indexes, Atlas Vector Search, aggregation pipelines. Sandbox limits that shape the code: **100 ops/sec**, 500 connections, 0.5 GB, 50-stage pipelines, a small cap on search/vector indexes (we use 2). See "Sandbox limits" in `docs/DATA_MODEL.md`; `db/SANDBOX_CHECK.md` records what was verified. Driver: `pymongo` (async API, `AsyncMongoClient`) in Python; the dashboard reads through the agent API, not directly.
- **Embeddings:** Voyage AI (`voyage-3.5`, 1024 dims). If no key is available, fall back to any 1024-dim model and keep the dimension in one constant.
- **Dashboard:** Next.js (App Router, TypeScript, Tailwind). Live updates via Server-Sent Events from the agent service, which relays change streams.

## Repo layout (target)

```
/agent          # agent loop, tools, policy gate, recovery planner
/services
  /freezer  /scheduler  /courier  /station   # mock lab systems
  /scanner                                   # independent verifier
/db
  validators/   # one JSON file per collection ($jsonSchema)
  check_sandbox.py  # verifies sandbox features, writes SANDBOX_CHECK.md
  indexes.py    # creates all indexes incl. TTL + vector search
  roles.md      # Atlas custom roles to create in the UI/CLI
  seed.py       # demo seed data (idempotent; --reset flag)
/dashboard      # Next.js app
/scripts        # demo helpers: revoke_grant.py, reset_demo.sh, run_all.sh
/docs
```

## Conventions

- **IDs:** human-readable string `_id`s for domain objects (`TS-1042`, `GRANT-0007`, `TR-0031`); `ObjectId` for ledger and event rows.
- **Time:** store UTC `datetime` (BSON Date), never strings. The out-of-freezer clock is computed from `samples.checked_out_at`.
- **Policy versions:** policies are immutable documents with `version`; the gate records `policy_id` + `version` on every decision.
- **Every ledger write** includes `actor`, `on_behalf_of`, `grant_id`, `policy_id`, `decision`, `timestamp`, `transfer_id`, `correlation_id`. The validator rejects writes without them.
- **Correlation IDs:** each tool call gets a `correlation_id`; the lab service echoes it, and the scanner includes it in its event so the agent can match `sent` to `confirmed`.
- **Config:** all settings via env vars (`.env`, never committed). `MONGODB_URI_AGENT`, `MONGODB_URI_SCANNER`, `MONGODB_URI_ADMIN`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, service ports.
- **Python:** type hints, Pydantic v2 models for every API body and document, `ruff` for lint/format. Keep services small: one file each is fine.
- **TypeScript:** strict mode; shared types in `dashboard/lib/types.ts` mirroring the Pydantic models.

## How to work in this repo

- This is a one-day hackathon build. **Demo first, polish later.** Follow the milestone order in `docs/BUILD_PLAN.md`; don't start a "stretch" item while an earlier milestone is broken.
- Keep the demo path working at all times. Before finishing a task, run `scripts/reset_demo.sh` and walk demo beat 1 (see `docs/DEMO.md`).
- Prefer simple, visible behavior over clever abstractions: judges score what they can see.
- When you add a tool for the agent, also add: its gate rules, its `reversible`/`irreversible` tag, its compensating action or recovery step, and its expected scanner event (if any).
- Don't mock out MongoDB features we claim to use. If something fails on the sandbox, record it in `db/SANDBOX_CHECK.md` and `docs/DATA_MODEL.md` and tell the team.
- Respect the sandbox's 100 ops/sec: one `AsyncMongoClient` per process, no per-second writes (courier positions stay in memory and go out over SSE), batch seed inserts with `insert_many`.

## Commands (fill in as they exist)

```bash
# one-time
python -m db.check_sandbox    # verify sandbox features → db/SANDBOX_CHECK.md
python -m db.indexes          # create validators + indexes
python -m db.seed --reset     # load demo data
# run everything
scripts/run_all.sh            # starts lab services, scanner, agent, dashboard
# demo helpers
python scripts/revoke_grant.py GRANT-0007
scripts/reset_demo.sh
```

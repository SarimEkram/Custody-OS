# Data model (MongoDB Atlas)

Database: `custody`. All times are BSON Dates in UTC.

## Atlas feature map

| Feature | Where we use it |
|---|---|
| Collections | `identities`, `grants`, `policies`, `samples`, `locations`, `voice_requests`, `transfers`, `ledger`, `custody_events`, `approvals`, `reservations`, `incidents`, `control` |
| GridFS | `voice_audio` bucket stores the original spoken request (short webm/opus clips) with a SHA-256 hash |
| `$jsonSchema` validation | `voice_requests` (speaker + transcript + audio hash required), `ledger` (identity + policy fields required), `custody_events` (`scanner_id` required), `grants`, `transfers` (state enum) |
| Roles (custom if the sandbox allows, else built-in) + app-level enforcement | agent can only insert/find in `ledger`; only scanner can insert `custody_events` |
| Change streams | `grants` (revocation → halt), `custody_events` (scan → advance state), `approvals` (sign-off → resume), `control` (Stop), all of the above → dashboard SSE |
| Transactions | custody handoff updates `samples` + `transfers` + inserts `ledger` atomically |
| TTL indexes | `grants.expires_at`, `reservations.expires_at`, pending `approvals.expires_at` |
| Vector Search | `ledger.embedding` (auditor NL search), `incidents.embedding` (risk flags in the gate) |
| Aggregation | custody chain rebuild, gap detection (`sent` without scan), exposure report |

## Collections and example documents

### `identities`
```json
{ "_id": "user:jlee", "kind": "human", "name": "Jordan Lee", "role": "lab_tech",
  "clearances": ["standard", "controlled"], "supervisor_id": "user:mokafor" }
{ "_id": "user:mokafor", "kind": "human", "name": "M. Okafor", "role": "supervisor",
  "clearances": ["standard", "controlled", "forensic"] }
{ "_id": "agent:custody-01", "kind": "agent", "name": "Custody Agent", "model": "openai/gpt-oss-120b" }
{ "_id": "scanner:reader-fzB", "kind": "scanner", "location_id": "FZ-B" }
```

### `grants` (delegation from a human to the agent)
```json
{
  "_id": "GRANT-0007",
  "grantor": "user:jlee",
  "grantee": "agent:custody-01",
  "status": "active",                       // active | revoked
  "scope": {
    "actions": ["lookup_sample","reserve_station","request_courier","notify",
                "checkout_sample","handoff_to_courier","deliver"],
    "sample_types": ["tissue", "evidence"],
    "locations": ["FZ-A","FZ-B","SEQ-3","FOR-LAB","CORR-1"],
    "max_restriction": "forensic"
  },
  "created_at": {"$date": "2026-11-14T17:00:00Z"},
  "expires_at": {"$date": "2026-11-14T23:00:00Z"},
  "revoked_at": null, "revoked_by": null, "revoke_reason": null
}
```
TTL deletes expired grants, but the TTL monitor runs about every 60 s, so **the gate must also check `expires_at` itself**. Each ledger decision embeds a `grant_snapshot` so the audit trail survives deletion.

### `policies` (immutable, versioned)
```json
{
  "_id": "custody-default@3", "policy_id": "custody-default", "version": 3, "active": true,
  "rules": {
    "forensic_requires_supervisor": { "restriction": "forensic", "tools": ["checkout_sample","handoff_to_courier","deliver"] },
    "certification_required": true,
    "checkout_preconditions": ["station_reserved","courier_assigned","eta_within_limit"],
    "handoff_scan_window_seconds": 120,
    "risk_similarity_threshold": 0.85
  },
  "created_by": "user:mokafor", "created_at": {"$date": "2026-11-14T16:00:00Z"}
}
{ "_id": "recovery@1", "policy_id": "recovery", "version": 1, "active": true,
  "rules": { "allowed_tools": ["cancel_reservation","cancel_courier_job","notify","return_to_storage"],
             "requires_valid_grant": false } }
```
To change a rule, insert a new version and flip `active`. Never edit an old one.

### `samples`
```json
{
  "_id": "EV-2201", "type": "evidence", "description": "DNA swab, case 24-1187",
  "restriction": "forensic",                // standard | controlled | forensic
  "required_certifications": ["FORENSIC"],
  "location_id": "FZ-B", "custodian": "location:FZ-B",
  "status": "stored",                       // stored | out | compromised | consumed
  "max_minutes_out": 20, "checked_out_at": null,
  "case_ref": "24-1187"
}
```

### `locations`
```json
{ "_id": "SEQ-3", "name": "Sequencing Station 3", "kind": "station",
  "certifications": ["BSL-2"], "map": {"x": 640, "y": 180},
  "travel_minutes": {"FZ-A": 6, "FZ-B": 4, "FOR-LAB": 8} }
```

### `voice_requests` (the original request, spoken or typed)
```json
{
  "_id": "VR-0019",
  "source": "voice",                        // voice | typed (fallback)
  "speaker_id": "user:jlee",                // logged-in identity, NOT inferred from the voice
  "audio_file_id": {"$oid": "..."},         // GridFS voice_audio file; null when typed
  "audio_sha256": "9f2c…",                  // integrity: re-hash the GridFS file to prove it's unchanged
  "audio_duration_secs": 4.2,
  "stt": {
    "provider": "elevenlabs", "model_id": "scribe_v3",
    "language_code": "eng", "language_probability": 0.99,
    "keyterms": ["TS-1042", "Freezer B", "Sequencing Station 3"],
    "transcription_id": "…"
  },
  "transcript": "Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM.",
  "words": [ {"text": "TS-1042", "start": 0.62, "end": 1.31, "logprob": -0.08} ],
  "low_confidence_terms": [],
  "parsed": { "sample_id": "TS-1042", "from": "FZ-B", "to": "SEQ-3", "window_start": {"$date": "2026-11-14T19:00:00Z"} },
  "transfer_id": "TR-0031",
  "created_at": {"$date": "2026-11-14T18:50:02Z"}
}
```
Store only `word`-type entries in `words` (drop `spacing`) to keep docs small. Typed requests set `source: "typed"`, `audio_file_id: null`, `audio_sha256: null`, `stt: null`.

### `transfers` (task state)
```json
{
  "_id": "TR-0031", "sample_id": "TS-1042",
  "from": "FZ-B", "to": "SEQ-3", "window_start": {"$date": "2026-11-14T19:00:00Z"},
  "requested_by": "user:jlee", "grant_id": "GRANT-0007",
  "request_id": "VR-0019", "source": "voice", "readback_confirmed": false,
  "blocked_reason": null,              // approval_pending | readback_pending
  "request_text": "Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM",
  "state": "in_transit",   // requested|reserved|checked_out|in_transit|received|blocked|revoked|recovering|recovered|cancelled
  "halt": false,
  "reservation_id": "RES-551", "courier_job_id": "CJ-208", "approval_id": null,
  "history": [ {"state": "requested", "at": {"$date": "..."}} ],
  "updated_at": {"$date": "..."}
}
```

### `ledger` (append-only)
```json
{
  "_id": {"$oid": "..."},
  "timestamp": {"$date": "2026-11-14T18:52:10Z"},
  "entry_type": "decision",          // intake | confirmation | decision | outcome | verification | compensation | recovery | exposure | violation | correction
  "transfer_id": "TR-0031", "sample_id": "TS-1042",
  "request_id": "VR-0019",           // links every action back to the original spoken/typed request
  "correlation_id": "c-7f3a",
  "actor": "agent:custody-01",
  "on_behalf_of": "user:jlee",
  "grant_id": "GRANT-0007",
  "grant_snapshot": { "status": "active", "expires_at": {"$date": "..."}, "scope": {"...": "..."} },
  "policy_id": "custody-default", "policy_version": 3,
  "tool": "checkout_sample", "args": {"sample_id": "TS-1042"},
  "reversible": false,
  "reason": "Station SEQ-3 reserved and courier CJ-208 assigned; ETA 4 min < 20 min limit.",
  "decision": "allow",               // allow | deny | pending_approval | pending_confirmation | halt
  "rule_hits": [], "risk_flags": [],
  "request": null, "response": null, // filled on outcome entries
  "status": null,                    // outcome: sent | confirmed | mismatch | unconfirmed
  "refs": [],                        // ObjectIds of related entries (decision ← outcome ← verification)
  "summary": "Agent checked out TS-1042 from FZ-B for jlee under GRANT-0007 (policy v3).",
  "embedding": [0.012, -0.044, "... 1024 floats"]
}
```
Write `summary` as one plain-English sentence per entry; it is what we embed for auditor search.

Voice-specific entries:
- `intake`: written when a request arrives. `actor` = `user:<speaker>`, `on_behalf_of` = same, `tool: null`, `decision: "allow"`, `response: {transcript, audio_sha256, model_id}`. Summary e.g. *"jlee requested by voice: 'Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM.'"*
- `confirmation`: written when the tech confirms the read-back. `response: {readback_text, confirmed_by, method: "button" | "voice", confirmation_request_id}`.

### `custody_events` (scanner only)
```json
{
  "_id": {"$oid": "..."},
  "scanner_id": "scanner:reader-fzB",
  "type": "checkout",                // checkout | handoff | intake | checkin | seal_check
  "sample_id": "TS-1042",
  "location_id": "FZ-B",
  "from_custodian": "location:FZ-B", "to_custodian": "courier:CJ-208",
  "correlation_id": "c-7f3a",
  "seal_id": "SEAL-88812",
  "scanned_at": {"$date": "2026-11-14T18:52:13Z"},
  "signature": "hmac-sha256:..."     // HMAC with scanner secret; agent can verify, not forge
}
```

### `approvals`
```json
{ "_id": "APR-0012", "transfer_id": "TR-0032", "sample_id": "EV-2201",
  "requested_for_tool": "checkout_sample", "reason_requested": "forensic_requires_supervisor",
  "approver_role": "supervisor", "status": "pending",   // pending | approved | denied
  "decided_by": null, "decided_at": null,
  "created_at": {"$date": "..."}, "expires_at": {"$date": "..."} }
```

### `reservations` (scheduler state, if stored in Atlas)
```json
{ "_id": "RES-551", "station_id": "SEQ-3", "transfer_id": "TR-0031",
  "status": "held", "expires_at": {"$date": "2026-11-14T19:30:00Z"} }
```

### `incidents` (past problems, for risk flagging)
```json
{ "_id": "INC-0004", "summary": "Forensic swab moved to uncertified station during shift change; chain challenged in court.",
  "tags": ["forensic", "certification", "shift_change"], "severity": "high",
  "embedding": ["... 1024 floats"] }
```

### `control`
```json
{ "_id": {"$oid": "..."}, "type": "stop", "scope": "all", "transfer_id": null,
  "issued_by": "user:mokafor", "at": {"$date": "..."} }
```

## Validators (`db/validators/*.json`)

### `ledger`
```json
{
  "$jsonSchema": {
    "bsonType": "object",
    "required": ["timestamp","entry_type","actor","on_behalf_of","policy_id","policy_version","decision","transfer_id","correlation_id"],
    "properties": {
      "timestamp":      { "bsonType": "date" },
      "entry_type":     { "enum": ["intake","confirmation","decision","outcome","verification","compensation","recovery","exposure","violation","correction"] },
      "actor":          { "bsonType": "string", "pattern": "^(agent|user|system):" },
      "on_behalf_of":   { "bsonType": "string", "pattern": "^user:" },
      "grant_id":       { "bsonType": ["string","null"] },
      "policy_id":      { "bsonType": "string" },
      "policy_version": { "bsonType": "int" },
      "decision":       { "enum": ["allow","deny","pending_approval","pending_confirmation","halt"] },
      "request_id":     { "bsonType": ["string","null"] },
      "status":         { "enum": ["sent","confirmed","mismatch","unconfirmed", null] },
      "reversible":     { "bsonType": ["bool","null"] },
      "transfer_id":    { "bsonType": "string" },
      "correlation_id": { "bsonType": "string" },
      "embedding":      { "bsonType": "array" }
    }
  }
}
```
Create with `validationLevel: "strict"`, `validationAction: "error"`. Demo moment: try to insert an entry without `on_behalf_of` from a shell and show the rejection.

### `custody_events`
```json
{
  "$jsonSchema": {
    "bsonType": "object",
    "required": ["scanner_id","type","sample_id","location_id","scanned_at","signature"],
    "properties": {
      "scanner_id": { "bsonType": "string", "pattern": "^scanner:" },
      "type":       { "enum": ["checkout","handoff","intake","checkin","seal_check"] },
      "sample_id":  { "bsonType": "string" },
      "location_id":{ "bsonType": "string" },
      "scanned_at": { "bsonType": "date" },
      "signature":  { "bsonType": "string" }
    }
  }
}
```

### `voice_requests`
```json
{ "$jsonSchema": { "bsonType": "object",
  "required": ["source","speaker_id","transcript","created_at"],
  "properties": {
    "source":       { "enum": ["voice","typed"] },
    "speaker_id":   { "bsonType": "string", "pattern": "^user:" },
    "transcript":   { "bsonType": "string", "minLength": 1 },
    "audio_file_id":{ "bsonType": ["objectId","null"] },
    "audio_sha256": { "bsonType": ["string","null"] },
    "created_at":   { "bsonType": "date" } } } }
```
App rule (not expressible cleanly in the validator): when `source == "voice"`, `audio_file_id`, `audio_sha256` and `stt` must be set. Check it in the Pydantic model. Like the ledger, `voice_requests` is insert-only from app code (except setting `transfer_id`/`parsed` once right after insert; do that in the same insert if possible).

### `transfers` (state enum)
```json
{ "$jsonSchema": { "bsonType": "object", "required": ["sample_id","from","to","requested_by","grant_id","state"],
  "properties": { "state": { "enum": ["requested","reserved","checked_out","in_transit","received","blocked","revoked","recovering","recovered","cancelled"] } } } }
```

### `grants`
```json
{ "$jsonSchema": { "bsonType": "object", "required": ["grantor","grantee","status","scope","expires_at"],
  "properties": { "status": { "enum": ["active","revoked"] }, "expires_at": { "bsonType": "date" } } } }
```

## Indexes (`db/indexes.py`)

```python
await db.grants.create_index("expires_at", expireAfterSeconds=0)            # TTL
await db.grants.create_index([("grantee", 1), ("status", 1)])
await db.reservations.create_index("expires_at", expireAfterSeconds=0)      # TTL
await db.approvals.create_index("expires_at", expireAfterSeconds=0,
                                partialFilterExpression={"status": "pending"})  # TTL only pending
await db.ledger.create_index([("transfer_id", 1), ("timestamp", 1)])
await db.ledger.create_index([("sample_id", 1), ("timestamp", 1)])
await db.ledger.create_index("correlation_id")
await db.custody_events.create_index([("sample_id", 1), ("scanned_at", 1)])
await db.custody_events.create_index("correlation_id")
await db.transfers.create_index("state")
await db.ledger.create_index("request_id")
await db.voice_requests.create_index([("speaker_id", 1), ("created_at", -1)])
# GridFS bucket "voice_audio" creates its own files/chunks indexes on first use
```

### Atlas Vector Search indexes

> **Update 2026-11-14:** we use **Atlas Automated Embedding** (`EMBEDDINGS_PROVIDER=atlas_auto`). The index field is `{"type": "autoEmbed", "modality": "text", "path": "summary", "model": "voyage-4"}` plus the filter fields; Atlas embeds `summary` on insert and `$vectorSearch` takes `query: {"text": ...}` with `path: "summary"`. No `embedding` field is stored. Supported models: voyage-4, voyage-4-large, voyage-4-lite, voyage-code-4, voyage-code-3 (not voyage-3.5). `db/indexes.py` builds both indexes; the `vector` definitions below are the `voyage` fallback.
Create via Atlas UI / Atlas CLI / `db.collection.create_search_index(SearchIndexModel(..., type="vectorSearch"))`.

`ledger_vector`, on `ledger`:
```json
{ "fields": [
  { "type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine" },
  { "type": "filter", "path": "sample_id" },
  { "type": "filter", "path": "decision" },
  { "type": "filter", "path": "entry_type" },
  { "type": "filter", "path": "timestamp" }
] }
```
`incidents_vector`, on `incidents`: same shape with filters on `tags` and `severity`.

Embedding dimension lives in one constant (`EMBED_DIM = 1024`). Embed `summary` on insert (agent side, before inserting; ledger is insert-only so no back-fill updates). Use Voyage `input_type="document"` for stored text and `"query"` for searches. Voyage is an embedding model, not an LLM. Provider comes from `EMBEDDINGS_PROVIDER`: `atlas_auto` lets Atlas Automated Embedding generate vectors (the app then makes no Voyage call; verify with the check script), `voyage` uses a key from Atlas → AI Model APIs → Model API Keys. Person A owns this; `ledger.append()` is the single place that embeds.

## Database users and roles

> **Update 2026-11-14:** layer 2 is **live**. We created two custom roles, `agentRole` and `scannerRole` (same privileges as the four below, grouped per user), plus users `agent_svc` and `scanner_svc`. Verified: `agent_svc` gets `13 Unauthorized` on any ledger update/delete. See `db/roles.md`.

We run on the hackathon-provided **Atlas Sandbox (Free cluster, formerly M0)**. See "Sandbox limits" below. Immutability is enforced in two layers, and the demo must work even if only the first layer is available:

1. **App-level (always on, primary):** only `agent/ledger.py` writes to `ledger`, and it exposes a single `append(entry)` function that calls `insert_one`. No other module imports the ledger collection for writing. There is no update/delete code path anywhere. A unit test greps the repo and fails if `ledger.update`, `ledger.replace`, `ledger.delete`, `find_one_and_update` or `bulk_write` on the ledger appears anywhere. The same pattern applies to `custody_events`: only `services/scanner` writes it.
2. **Database-level (add if it works on the sandbox, ~15 min to try):** Atlas docs say Free clusters support custom roles (changes can take up to 30 s to deploy). Create the custom roles below in Atlas → Database Access → Custom Roles. If the UI refuses a privilege, fall back to built-in roles: `agent_svc` and `scanner_svc` get `readWrite@custody`, and we rely on layer 1 plus the validators. Record which path we took in `db/roles.md` and say it honestly in the pitch.

| Custom role | Privileges |
|---|---|
| `ledgerAppender` | `custody.ledger`: `insert`, `find`, `changeStream` (no `update`, `remove`) |
| `custodyEventsReader` | `custody.custody_events`: `find`, `changeStream` |
| `custodyEventsWriter` | `custody.custody_events`: `insert`, `find` |
| `agentState` | `find`, `insert`, `update`, `changeStream` on `transfers`, `samples`, `approvals`, `reservations`, `control`; `find`, `insert` on `voice_requests`, `voice_audio.files`, `voice_audio.chunks`; `find`, `changeStream` on `grants`, `policies`, `identities`, `locations`, `incidents` |

| DB user | Roles | Used by |
|---|---|---|
| `agent_svc` | `ledgerAppender`, `custodyEventsReader`, `agentState` | agent service (`MONGODB_URI_AGENT`) |
| `scanner_svc` | `custodyEventsWriter`, read on `samples`, `locations`, `identities` | scanner (`MONGODB_URI_SCANNER`) |
| `admin_svc` | `readWrite` on `custody` + `dbAdmin` | seed/index scripts, revoke script, dashboard approval writes via agent API (`MONGODB_URI_ADMIN`) |

Demo moment: with `agent_svc`, run `db.ledger.updateOne(...)` → `not authorized` (only if layer 2 worked; otherwise show the validator rejection and the grep test instead). Note: revoking grants should go through `admin_svc` (the agent must not be able to edit its own grants).

## Sandbox limits (Atlas Free cluster)

> **Update 2026-11-14:** we're on an **M10** cluster (MongoDB 9.0.7), not the free sandbox, so the limits below don't bind us. Kept for reference. Everything we use is verified in `db/CLUSTER_CHECK.md`.

From the Atlas "Free Cluster Limits" docs (https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/). Confirm the tier on the cluster's Overview page; if the sandbox turns out to be Flex or dedicated, these limits are looser and nothing below changes.

| Limit | Value | What it means for us |
|---|---|---|
| MongoDB version | 8.0 (fixed) | Fine for everything we use. |
| Storage | 0.5 GB (docs + indexes) | Fine. Embeddings are ~4 KB each as float arrays; thousands of ledger rows fit easily. Don't log raw HTTP bodies larger than needed. |
| Throughput | **100 ops/sec**, then throttled | **Don't write courier position ticks to Atlas.** Keep live position in the courier service's memory and push it to the dashboard over SSE. Only custody-relevant events go to the DB. |
| Connections | 500 max | One shared `AsyncMongoClient` per process. Never create a client per request. |
| Collections | 500 total, 100 DBs | Fine (we use ~15 incl. GridFS). |
| Audio storage | counts toward 0.5 GB | A 5-second webm/opus clip is ~10–40 KB, so hundreds of requests fit. Cap uploads at 30 s / 1 MB in the API. |
| Change streams | Supported. Namespace (`ns`) filters on database names can only use strings/regex, not `$in` | We open streams on individual collections (`db.grants.watch(...)`), so this doesn't bite. Don't build a single `client.watch()` with `$in` on `ns.db`. |
| Aggregation | Max 50 stages; `allowDiskUse` ignored; 32 MB in-memory sort; no `$where`, map-reduce or server-side JS | Our pipelines are short. Always `$match` before `$sort`, and project out `embedding` early. |
| Custom roles | Supported; changes take up to 30 s to deploy | Wait before testing a new role. |
| Search / Vector Search | Supported on shared infra, no dedicated search nodes; Free clusters have a small cap on the number of search + vector indexes (check the Atlas Search limits page) | We need exactly **2** vector indexes (`ledger_vector`, `incidents_vector`). Don't create extra Atlas Search indexes. If only one fits, drop `incidents_vector` (risk flagging is the first cut anyway). |
| Data transfer | 10 GB in / 10 GB out per rolling 7 days | Fine. |
| Auth | SCRAM password, X.509, AWS IAM | Use SCRAM users per service. |

Transactions and TTL indexes aren't listed as restricted for Free clusters, but **verify them with the check script** rather than trusting this doc.

### Cluster check script (`db/check_cluster.py`), run in the first hour

Person A runs this right after creating the users. Each check prints PASS/FAIL and the script cleans up after itself (uses a `_check` prefix on collections).

1. **Connect** with each of the three URIs; `ping`.
2. **Validator:** create `_check_ledger` with the ledger validator; insert a valid doc (PASS) and one missing `on_behalf_of` (expect `DocumentFailedValidation`).
3. **Roles:** as `agent_svc`, try `update_one` on `ledger` (expect `OperationFailure` code 13 *Unauthorized* if custom roles are in place; otherwise print "layer 2 not active, relying on app-level").
4. **Change stream:** open `watch()` on `_check_grants`, insert + update a doc from a second client, expect both events within 5 s.
5. **Transaction:** start a session + transaction, write to two collections, commit; then a second transaction that aborts, and confirm nothing was written.
6. **TTL:** create a TTL index on `_check_ttl.expires_at`, insert a doc expiring now, poll up to ~90 s for deletion (the TTL monitor runs about every 60 s). Run this in the background; don't block on it.
7. **Vector Search:** create a `vectorSearch` index on `_check_vec` (`numDimensions: 1024`), wait for status `READY` via `list_search_indexes()`, insert 3 docs with random vectors, run `$vectorSearch`, expect 3 results. Then try creating a second vector index to confirm the cap allows two.
8. Print a summary table and write it to `db/CLUSTER_CHECK.md` so the team (and Claude) knows what's actually available.

If any check fails, tell the team immediately and update this doc's feature map. Fallbacks: no custom roles → app-level layer only; only one vector index → keep `ledger_vector`; change streams or transactions failing would be a surprise, so ask the organizers before redesigning.

## Change streams

```python
# 1. Revocation → halt
pipeline = [{"$match": {"$or": [
    {"operationType": "update", "updateDescription.updatedFields.status": "revoked"},
    {"operationType": "delete"}                       # TTL expiry
]}}]
async with await db.grants.watch(pipeline, full_document="updateLookup") as stream:
    async for ch in stream:
        grant_id = ch["documentKey"]["_id"]
        await halt_transfers_for_grant(grant_id)      # sets transfers.halt = true, state = revoked

# 2. Scan → advance state (verifier)
pipeline = [{"$match": {"operationType": "insert"}}]
async with await db.custody_events.watch(pipeline) as stream:
    async for ch in stream:
        await on_scan(ch["fullDocument"])             # match correlation_id, run handoff txn

# 3. Approval → resume
pipeline = [{"$match": {"operationType": "update",
                        "updateDescription.updatedFields.status": {"$in": ["approved", "denied"]}}}]
# 4. Stop switch
pipeline = [{"$match": {"operationType": "insert", "fullDocument.type": "stop"}}]
```
Store each stream's `resume_token` (in memory is fine; in a `stream_state` collection if time allows) so a restart doesn't miss a revocation. The dashboard SSE endpoint is one more watcher over `ledger`, `transfers`, `custody_events`, `approvals`.

## Transaction: verified custody handoff

```python
async def apply_verified_scan(client, scan: dict, transfer: dict, next_state: str):
    async with client.start_session() as s:
        async with await s.start_transaction():
            db = client.custody
            await db.samples.update_one(
                {"_id": scan["sample_id"]},
                {"$set": {"location_id": scan["location_id"],
                          "custodian": scan["to_custodian"],
                          **({"status": "out", "checked_out_at": scan["scanned_at"]} if scan["type"] == "checkout" else {}),
                          **({"status": "stored", "checked_out_at": None} if scan["type"] == "checkin" else {})}},
                session=s)
            res = await db.transfers.update_one(
                {"_id": transfer["_id"], "state": transfer["state"]},   # optimistic guard
                {"$set": {"state": next_state, "updated_at": now()},
                 "$push": {"history": {"state": next_state, "at": now(), "scan_id": scan["_id"]}}},
                session=s)
            if res.modified_count != 1:
                raise StateConflict(transfer["_id"])
            await db.ledger.insert_one(verification_entry(scan, transfer, status="confirmed"), session=s)
```

## Aggregations

### Custody chain for a sample (auditor timeline)
```python
[
  {"$match": {"sample_id": "EV-2201"}},
  {"$unionWith": {"coll": "custody_events",
                  "pipeline": [{"$match": {"sample_id": "EV-2201"}},
                               {"$addFields": {"timestamp": "$scanned_at", "entry_type": "scan"}}]}},
  {"$sort": {"timestamp": 1}},
  {"$project": {"embedding": 0}}
]
```

### Gaps: `sent` with no matching scan
```python
[
  {"$match": {"entry_type": "outcome", "status": "sent", "reversible": False}},
  {"$lookup": {"from": "custody_events", "localField": "correlation_id",
               "foreignField": "correlation_id", "as": "scans"}},
  {"$match": {"scans": {"$size": 0}}},
  {"$project": {"transfer_id": 1, "sample_id": 1, "tool": 1, "timestamp": 1, "on_behalf_of": 1}}
]
```
(Ledger outcome entries are never updated, so "sent" stays on the outcome row; confirmation is a separate `verification` entry. That's why the gap query joins on scans, not on a status field.)

### Exposure report
```python
[
  {"$match": {"entry_type": "exposure"}},
  {"$group": {"_id": "$sample_id", "total_minutes_out": {"$sum": "$response.minutes_out"},
              "excursions": {"$sum": {"$cond": ["$response.exceeded", 1, 0]}}}}
]
```

### From any action back to the spoken request
```python
[
  {"$match": {"transfer_id": "TR-0031"}},
  {"$sort": {"timestamp": 1}},
  {"$lookup": {"from": "voice_requests", "localField": "request_id", "foreignField": "_id",
               "pipeline": [{"$project": {"words": 0}}], "as": "request"}},
  {"$project": {"embedding": 0}}
]
```
The auditor UI shows the transcript with a play button (streams the GridFS audio via `GET /voice/requests/{id}/audio`) and re-checks `audio_sha256`.

### Restricted moves without a supervisor
```python
[
  {"$match": {"entry_type": "decision", "decision": "allow",
              "tool": {"$in": ["checkout_sample", "handoff_to_courier", "deliver"]}}},
  {"$lookup": {"from": "samples", "localField": "sample_id", "foreignField": "_id", "as": "s"}},
  {"$match": {"s.restriction": "forensic"}},
  {"$lookup": {"from": "approvals", "let": {"t": "$transfer_id"},
               "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$transfer_id", "$$t"]},
                                                          {"$eq": ["$status", "approved"]}]}}}],
               "as": "appr"}},
  {"$match": {"appr": {"$size": 0}}}
]
```
Should return zero rows. Show that live: it's the proof the gate works.

## Auditor natural-language search

1. The LLM turns the question into `{semantic_query, filters}` (e.g. `filters: {decision: "deny"}`), using a fixed JSON schema (tool use with a single `audit_query` tool). Never let the model write raw pipelines.
2. Embed `semantic_query` (Voyage, `input_type="query"`).
3. Run:
```python
[
  {"$vectorSearch": {"index": "ledger_vector", "path": "embedding", "queryVector": qv,
                     "numCandidates": 200, "limit": 20, "filter": filters}},
  {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}}
]
```
4. For questions that map to a canned aggregation (gaps, restricted-without-supervisor, exposure), route to that aggregation instead; it's exact and more convincing.
5. The LLM writes a short answer citing ledger `_id`s; the UI links each to the timeline.

## Risk flagging in the gate (first thing to cut)

On irreversible tools, embed a one-line description of the planned action, `$vectorSearch` `incidents_vector` with `limit: 3`. If top score ≥ `risk_similarity_threshold`, add `risk_flags: ["similar_to:INC-0004"]` and require approval.

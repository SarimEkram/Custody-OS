# Database roles and users

Created 2026-11-14 with the Atlas CLI on the project's M10 cluster (`cluster0.fmdhjr.mongodb.net`).
**Layer 2 (database-level) immutability is active**: custom roles work, so `agent_svc` physically cannot edit or delete the ledger.
Layer 1 (app-level: only `agent/ledger.py` → `append()` → `insert_one`) still applies on top.

We use two custom roles (one per service user) instead of the four listed in `DATA_MODEL.md`; the privileges are the same.

## Custom roles (database `custody`)

### `agentRole` (user `agent_svc`, env `MONGODB_URI_AGENT`)
| Collections | Actions |
|---|---|
| `ledger` | `FIND`, `INSERT`, `CHANGE_STREAM` (no `UPDATE`, no `REMOVE`) |
| `custody_events`, `grants`, `policies`, `identities`, `locations`, `incidents` | `FIND`, `CHANGE_STREAM` |
| `transfers`, `samples`, `approvals`, `reservations`, `control` | `FIND`, `INSERT`, `UPDATE`, `CHANGE_STREAM` |
| `voice_requests`, `voice_audio.files`, `voice_audio.chunks` | `FIND`, `INSERT`, `CREATE_INDEX` (GridFS creates its indexes on first use) |

### `scannerRole` (user `scanner_svc`, env `MONGODB_URI_SCANNER`)
| Collections | Actions |
|---|---|
| `custody_events` | `FIND`, `INSERT` |
| `samples`, `locations`, `identities` | `FIND` |

## Users

| User | Role | Used by |
|---|---|---|
| `agent_svc` | `agentRole` | agent service |
| `scanner_svc` | `scannerRole` | scanner service (only writer of `custody_events`) |
| `admin_svc` | `readWrite@custody` + `dbAdmin@custody` (narrowed 2026-11-14; `db.indexes` and the full test suite verified; creating *new* search indexes untested under this role) | setup scripts, seed, indexes, revoke script |

The team's personal `*_db_user` logins are `atlasAdmin` and are for setup only; the app never uses them.

## Verified (2026-11-14)

```
PASS  agent   insert ledger                  -> allowed
PASS  agent   update ledger                  -> refused (13 Unauthorized)
PASS  agent   delete ledger                  -> refused (13 Unauthorized)
PASS  agent   insert custody_events          -> refused (13 Unauthorized)
PASS  agent   update grants                  -> refused (13 Unauthorized)
PASS  agent   insert transfers               -> allowed
PASS  scanner insert custody_events          -> allowed
PASS  scanner insert ledger                  -> refused (13 Unauthorized)
```

## Recreate (Atlas CLI, run under bash)

```bash
atlas auth login --authType UserAccountLegacy   # device-code flow; the newer browser flow returned "not authorized"

P=""; add(){ for c in $2; do for a in $1; do P="$P,$a@custody.$c"; done; done; }
add "FIND INSERT CHANGE_STREAM" "ledger"
add "FIND CHANGE_STREAM" "custody_events grants policies identities locations incidents"
add "FIND INSERT UPDATE CHANGE_STREAM" "transfers samples approvals reservations control"
add "FIND INSERT CREATE_INDEX" "voice_requests voice_audio.files voice_audio.chunks"
atlas customDbRoles create agentRole --privilege "${P#,}"

P=""; add "FIND INSERT" "custody_events"; add "FIND" "samples locations identities"
atlas customDbRoles create scannerRole --privilege "${P#,}"

atlas dbusers create --username agent_svc   --password '<generated>' --role agentRole
atlas dbusers create --username scanner_svc --password '<generated>' --role scannerRole
```

If you add a collection the agent needs, add it to `agentRole` (`atlas customDbRoles update agentRole --append --privilege ...`) and note it here.

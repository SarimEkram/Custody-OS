# Cluster check

Run 2026-11-14 17:42 UTC by `python -m db.check_cluster`. 19/19 passed.

| Check | Result | Detail |
|---|---|---|
| connect admin_svc | PASS |  |
| connect agent_svc | PASS |  |
| connect scanner_svc | PASS |  |
| server version / replica set | PASS | 9.0.7 / atlas-vy36vs-shard-0 |
| validator accepts valid ledger entry | PASS |  |
| validator rejects entry w/o on_behalf_of | PASS | code 121 DocumentValidationFailure |
| agent cannot update ledger | PASS | expect 13 Unauthorized |
| agent cannot delete ledger | PASS |  |
| agent cannot write custody_events | PASS |  |
| agent cannot edit grants | PASS |  |
| scanner cannot write ledger | PASS |  |
| change stream insert + update | PASS | got ['insert', 'update'] |
| transaction commit (2 collections) | PASS |  |
| transaction abort leaves no writes | PASS |  |
| Voyage embeddings (ai.mongodb.com) | PASS | voyage-3.5, 1024 dims |
| GridFS voice_audio upload/download (agent_svc) | PASS | 1500 bytes |
| vector search index + $vectorSearch | PASS | 3 results, index ready in 41s |
| TTL index deletes expired doc | PASS | deleted after 46s |
| automated embedding text query | PASS | top hit _id=1 (expected 1) |

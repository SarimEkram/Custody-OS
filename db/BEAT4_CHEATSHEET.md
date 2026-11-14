# Beat 4 cheat sheet: "the agent can't rewrite history"

Verified 2026-11-14 as `agent_svc` (the agent's own DB login). Use `MONGODB_URI_AGENT` from `.env`.

```bash
# open a shell as the agent without printing the password
mongosh "$(grep '^MONGODB_URI_AGENT=' .env | cut -d= -f2- | awk '{print $1}')"
```

| Type this | Expected | What to say |
|---|---|---|
| `db.ledger.find({}, {summary:1, decision:1}).sort({timestamp:-1}).limit(3)` | rows | "The agent can read its own record." |
| `db.ledger.updateOne({}, {$set: {decision: "allow"}})` | `MongoServerError: not authorized ... (13)` | "It can't edit history. The database refuses, not our code." |
| `db.ledger.deleteOne({})` | `not authorized (13)` | "Or delete it." |
| `db.ledger.insertOne({tool: "x"})` | `Document failed validation (121)` | "It can add, but only entries that say who acted, for whom, under which grant and policy." |
| `db.custody_events.insertOne({scanner_id: "scanner:fake"})` | `not authorized (13)` | "And it can't forge a scanner confirmation." |
| `db.grants.updateOne({_id: "GRANT-0007"}, {$set: {status: "active"}})` | `not authorized (13)` | "It can't un-revoke its own permission." |

Before going on stage: run it once, and keep the terminal open as the `agent_svc` shell. Nothing here changes data.

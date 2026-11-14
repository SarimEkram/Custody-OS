# Restoring the agent's authority: what the dashboard needs from the backend

**Owner of the backend part: Person A** (grants, roles, ledger). The dashboard and the mock already implement
the flow below, so this is the contract to build to. Nothing here changes a documented endpoint.

## Why this exists

After a grant is revoked the agent can't act, and new requests are denied (`grant_revoked`). The only way back
was `scripts/reset_demo.sh`. This adds a deliberate, audited way to restore authority from the dashboard.

## The security model (please keep all of these)

1. **Issue a new grant. Never reactivate the revoked one.** The revoked grant stays `revoked` forever, which keeps
   the audit trail and the append-only story intact. The new grant has `supersedes: "<old id>"`.
2. **Supervisor only.** The identity must be a human with the `supervisor` role. A technician, the agent
   (`agent:*`), and the grantor acting alone are all refused. This is separation of duties: a revoke may mean
   something went wrong, so the person the agent works for can't quietly undo it. The agent must never be able to
   grant itself authority.
3. **The server decides the contents.** The client sends only `{by, reason}`. Scope is copied from the revoked
   grant and can never be widened. Expiry is set by the server (short, capped; the mock uses 60 min). Any
   `scope` / `expires_at` in the request body is ignored.
4. **A reason is required** (min 8 characters) and is saved permanently in the ledger.
5. **It is recorded** as an append-only ledger entry under the supervisor's name (see below).
6. **It never resumes halted transfers.** They stay `revoked` / `cancelled` / `recovered`. New requests start new
   transfers, which the gate will now allow under the new grant.
7. **Write with `admin_svc`.** The agent's DB user must not be able to create or edit grants (same rule as revoke).
8. **Atomic and safe to retry.** Create the grant, mark the old one `superseded_by`, and append the ledger row in one
   transaction. A second re-issue of the same revoked grant must fail.

## Endpoint

`POST /grants/{revoked_grant_id}/reissue`

Request:

```json
{ "by": "user:mokafor", "reason": "Revoked during a rehearsal. Reviewed with the technician." }
```

`200` response:

```json
{ "ok": true, "grant_id": "GRANT-0008", "supersedes": "GRANT-0007", "expires_at": "2026-11-14T20:00:00Z" }
```

Errors use `{"detail": "<readable message>"}`, which the dashboard shows as-is:

| Status | When | Message the mock uses |
|---|---|---|
| 403 | `by` is not a supervisor | `Only a supervisor can issue a new grant.` |
| 403 | `by` is an agent | `The agent cannot grant itself authority.` |
| 404 | unknown grant | `unknown grant GRANT-xxxx` |
| 409 | old grant is still `active` | `GRANT-0007 is still active, so there is nothing to replace.` |
| 409 | old grant already replaced | `GRANT-0007 was already replaced by GRANT-0008.` |
| 409 | another active grant exists for the agent | `An active grant already exists for this agent.` |
| 422 | reason missing or shorter than 8 chars | `A reason of at least 8 characters is required.` |

## New grant document

Same shape as `GRANT-0007` plus: `supersedes` (the old id) and `issued_by` (the supervisor). The old grant gains
`superseded_by`. Add both to the `grants` validator and `agent/models.py`.

## Ledger entry (append via `ledger.append()`)

| Field | Value |
|---|---|
| `entry_type` | `correction` (it references the old grant, as rule 4 in CLAUDE.md describes) |
| `actor` | the supervisor, e.g. `user:mokafor` |
| `on_behalf_of` | the original grantor, e.g. `user:jlee` |
| `grant_id` | the **new** grant id |
| `decision` | `allow` |
| `rule_hits` | `["grant_reissued"]` (**the dashboard keys off this exact string**) |
| `reason` | the supervisor's reason |
| `response` | `{ "supersedes": "<old id>", "expires_at": "<iso>" }` |
| `summary` | one plain sentence, e.g. "mokafor issued GRANT-0008 to replace revoked GRANT-0007. Same scope, expires in 60 min. Reason: ..." |

**Open point:** the ledger validator requires `transfer_id`. This entry isn't about a transfer. Either allow a
placeholder such as `"SYSTEM"` or the most recently halted transfer's id (the mock uses the latest transfer, else
`SYSTEM`), or relax the validator for this entry type.

## How the dashboard decides the agent has authority again

There is no grant event on the SSE stream, so it reads the ledger: a `halt`/`deny` entry whose `rule_hits` include
`grant_revoked`, `grant_expired` or `grant_missing` means no authority, until it sees either a `grant_reissued`
entry or the gate allowing an agent action. If `GET /state` returns `grants`, an `active` grant also counts.

## Tests worth adding (the mock and dashboard already check these behaviours)

- technician, agent and grantor-only identities get 403
- reason too short gets 422; active grant gets 409; double re-issue gets 409
- scope and expiry in the body are ignored; the new scope equals the old scope
- the old grant is still `revoked` afterwards; the new one `supersedes` it
- the ledger row exists with `rule_hits: ["grant_reissued"]` and the supervisor as `actor`
- a transfer started after the re-issue passes the gate; a halted transfer does not resume

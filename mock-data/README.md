# Mock agent service

A zero-dependency stand-in for the agent service (`:8000`), so the dashboard flow can be rehearsed
before the real backend exists. **Everything here is simulated.** It is not the policy gate, not a
scanner, and writes nothing to MongoDB. Do not use it for the real demo.

## Run

```bash
# terminal 1
node mock-data/server.mjs          # http://localhost:8000
# terminal 2
cd dashboard && npm run dev        # http://localhost:5173
```

Options (env vars): `PORT` (default 8000), `MOCK_SPEED` (default 1; `2` = twice as fast, `0.5` = slower).

## What it simulates

It follows the documented contracts: a decision entry is written before each action, the outcome is
`sent`, a scanner event arrives about a second later, then a verification entry. State only advances
after the scan.

| Endpoint | Behaviour |
|---|---|
| `GET /stream` | SSE with `{kind, data}` messages (`ledger`, `transfer`, `scan`, `approval`, `confirmation_request`, `courier_position`) |
| `GET /state`, `GET /health` | Snapshot for reconnect resync; health check |
| `POST /transfers` | Typed request. Text is parsed for a sample ID, optional `from` / `to` |
| `POST /voice/requests` | Ignores the audio and returns a scripted transcript, cycling through the 4 below |
| `POST /transfers/{id}/confirm` | Releases the read-back. Returns `409` if the transfer was halted (e.g. grant revoked) |
| `POST /approvals/{id}` | Only `user:mokafor` may decide (others get 403) |
| `POST /control/stop`, `POST /grants/{id}/revoke` | Halt, roll back reversible steps, recover the sample if it is out |
| `POST /audit/query` | Canned answers: sample ID, `GRANT-0007`, "gaps", "without supervisor" |
| `POST /mock/reset` | Clears all mock state (mock-only helper) |
| `GET /voice/requests/{id}/audio` | Not implemented, so the audio player shows "unavailable" |

## Test script

Each voice press (mic button) returns the next transcript in this loop:

1. `Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM.` → happy path.
2. `Move EV-2201 to the Forensics Lab now.` → forensic block, needs approval.
3. `Move TS-1077 from Freezer A to Sequencing Station 3.` → 3-minute clock, for the revoke test.
4. `Move TS-1024 ...` → unknown ID, exercises the clarification flow (no transfer is created).

Typed requests work the same, so you can also type those sentences.

### Beat 1: normal run
Send request 1. Wait for the read-back card (about 4 s), click **Confirm transfer**. Watch checkout go
SENT → CONFIRMED, the dot move through the corridor, then `received`.

### Beat 2: blocked, then approved
Send request 2 and confirm the read-back. The ledger shows **PENDING APPROVAL**, and the approval appears
in the inbox. Try Approve as Jordan Lee (it is disabled, and the mock would return 403), then switch the
identity to M. Okafor and approve. The run resumes.

### Beat 3: revoked mid-transfer
Send request 3, confirm, and wait until the state is `in_transit`. Click **Revoke grant** (or **STOP AGENT**).
You should see the halt row, the rolled-back reservation, courier job and notice, then the recovery route,
a scanner check-in, and an exposure entry.

### Revoke while a read-back is waiting
Send a request and, before confirming, click **Revoke grant**. The card is replaced by a notice that the
transfer was stopped before you confirmed it and nothing left storage. A new request is then denied
(`grant_not_active`) until you reset the mock (`POST /mock/reset`).

### Beat 4: auditor
In the Auditor view, ask `What happened to TS-1077?`, `Find custody gaps.` or
`Show restricted transfers without supervisor approval.` (zero rows after a normal beat 2).

## Limits

- State is in memory. Restarting the server (or `POST /mock/reset`) clears it; reload the dashboard after.
- Courier coordinates use the same 760×420 space as the dashboard map.
- Authorization is only a minimal simulation (approval by supervisor).

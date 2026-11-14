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

It now speaks the **real backend's shapes** (checked by running the backend's own route code), so what works here
works there: `confirmation_request` is `{id, transfer_id, text}`, `courier_position` is progress along the current
leg with no coordinates (and `from === to` on the recovery leg), intake explains problems in `problems[]`, a
requester with no active grant gets `403 "<user> has no active grant for the agent"` before anything is stored, only
the requester can confirm, and audit queries return the same row shapes as the real pipelines.

It follows the documented contracts: a decision entry is written before each action, the outcome is
`sent`, a scanner event arrives about a second later, then a verification entry. State only advances
after the scan.

| Endpoint | Behaviour |
|---|---|
| `GET /stream` | SSE with `{kind, data}` messages (`ledger`, `transfer`, `scan`, `approval`, `confirmation_request`, `courier_position`) |
| `GET /state`, `GET /health` | Snapshot for reconnect resync; health check |
| `POST /transfers` | Typed request. Text is parsed for a sample ID, optional `from` / `to` |
| `POST /voice/requests` | Ignores the audio and returns a scripted transcript, cycling through the 4 below. Only `user:jlee` holds a grant, as in the real seed |
| `POST /transfers/{id}/confirm` | Releases the read-back. Returns `409` if the transfer was halted (e.g. grant revoked) |
| `POST /approvals/{id}` | Only `user:mokafor` may decide (others get 403) |
| `POST /grants/{id}/reissue` | Issues a NEW grant to replace a revoked one. Supervisor only (`by: user:mokafor`); refuses the agent; copies scope; fixed 60 min expiry; reason required; ledgered as `grant_reissued`. Spec: `dashboard/GRANT_REISSUE_SPEC.md` |
| `POST /control/stop`, `POST /grants/{id}/revoke` | Halt, roll back reversible steps, recover the sample if it is out |
| `POST /audit/query` | Canned answers: sample ID, `GRANT-0007`, "gaps", "without supervisor" |
| `GET /audit/transfer/{id}`, `GET /audit/requests/{id}/verify` | Transfer trace (each row joined to its request) and audio hash re-check |
| `GET /transfers/{id}/readback/audio` | A short tone standing in for the spoken read-back (real: MP3 from text-to-speech). 409 when nothing is pending; 404 when TTS is off |
| `POST /transfers/{id}/confirm/voice` | The **proposed** voice-confirmation contract. Decides on the server: "confirm" / "yes, confirm" confirm; "cancel", "don't confirm", "no" do not. Requester only. Ledger records `method: "voice"` and the transcript |
| `POST /mock/heard` `{text}` | Test switch: what the mock "hears" next from `confirm/voice` (default `confirm`) |
| `POST /mock/tts` `{on}` / `POST /mock/voice-confirm` `{on}` | Test switches: spoken read-back on/off (also env `MOCK_TTS=0`); the `confirm/voice` route on/off (off = 404 "Not Found", like the real backend today) |
| `POST /mock/seed-gap` | Test helper: an irreversible action that was sent but never scanned, so "Find custody gaps" has something to find |
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
(`grant_revoked`) until you reset the mock (`POST /mock/reset`).

### Restore authority after a revoke
After the revoke above, click **Restore authority** (banner or denial card). As Jordan Lee you'll be told only a
supervisor can; use the button in the dialog to switch to M. Okafor, give a reason, tick the box and issue. A new
grant (`GRANT-0008`) replaces the revoked one (`GRANT-0007` stays revoked) and new requests work again.
`POST /mock/reset` still resets everything.

### Beat 4: auditor
In the Auditor view, ask `What happened to TS-1077?`, `Find custody gaps.` or
`Show restricted transfers without supervisor approval.` (zero rows after a normal beat 2).

## Limits

- State is in memory. Restarting the server (or `POST /mock/reset`) clears it; reload the dashboard after.
- Courier coordinates use the same 760×420 space as the dashboard map.
- Authorization is only a minimal simulation (approval by supervisor).

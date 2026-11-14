# CustodyOS dashboard

SPA (React + TypeScript, Vite, Tailwind v4, Zustand, Lucide). It only talks to the agent service
REST API and its SSE stream. It never connects to MongoDB or ElevenLabs.

## Run

```bash
cd dashboard
npm install
npm run dev        # http://localhost:5173  (use localhost: the mic needs a secure context)
npm run build      # tsc -b && vite build
npm run lint
npm test           # vitest
```

`/api/*` is proxied to `http://localhost:8000` with the `/api` prefix stripped
(`/api/voice/requests` → FastAPI `POST /voice/requests`). Override the base URL with `VITE_API_BASE`.

## Structure

```
src/app/         App + AppShell (error boundary, offline banner, stream hook)
src/pages/       ControlCenter, AuditorPage, DemoPage
src/components/  layout, command (mic, typed box, read-back), map, workflow, ledger, approvals, auditor, demo, ui
src/hooks/       useEventStream (one SSE connection, backoff, resync), useMediaRecorder, useNow, useAgentStatus
src/lib/         api.ts (only REST module), types.ts, normalize.ts, format.ts, auditLocal.ts, seed.ts
src/store/       custodyStore.ts (Zustand; mirrors backend state, never decides authorization)
```

## Dev-server note (Windows)

If the page behaves as though a file wasn't updated (for example "x is not a function" for something that clearly
exists), Vite's file watcher may have cached a half-written file. Restart it with `npm run dev -- --force`.
A production build (`npm run build`) is not affected.

## Integration status

Verified here: `tsc`, `oxlint` (warnings only), `vitest` (31 tests), `vite build`.
**Not verified:** nothing has been run against a live backend, and the UI has not been exercised in a browser yet.

| Feature | Status |
|---|---|
| Typed request `POST /transfers` | Implemented, awaiting backend. Body assumed `{text, user_id}` |
| Voice `POST /voice/requests` (multipart `audio`, `user_id`) | Implemented per contract, awaiting backend |
| Read-back `POST /transfers/{id}/confirm` | Implemented, awaiting backend |
| Approvals `POST /approvals/{id}` | Implemented, awaiting backend. Never optimistic: waits for the SSE `approval` event |
| Stop `POST /control/stop`, revoke `POST /grants/{id}/revoke` | Implemented, awaiting backend. Halt is shown only when the backend reports it |
| SSE `GET /stream` (ledger, transfer, scan, approval, confirmation_request, courier_position) | Implemented, awaiting backend. Dedupes by id, auto-reconnects |
| Auditor `POST /audit/query` | Implemented, awaiting backend. Falls back to the live in-memory ledger, clearly labelled |
| Exposure clock (counts up toward the limit) | Implemented. Starts only from a scanner `checkout` event; keeps running through recovery until a scanner `checkin` |
| Map | Implemented. Sample positions change only on scanner events |
| Demo Director | Scenarios run as typed requests. Reset shows the documented command only |
| Original audio playback | Implemented, awaiting `GET /voice/requests/{id}/audio` |
| Read-back TTS | Plays `audio_url` if the backend sends one; text read-back always works |

## Backend requirements (for the teammates who own those parts)

1. **`GET /state`** returning `{transfers, ledger, scans, approvals, samples, locations, grants, demo_scripted, demo_speed}` so the UI can resync after an SSE reconnect and load locations/samples/max_minutes_out. Without it the UI uses seed reference data for map layout and exposure limits.
2. **`GET /health`** for the backend-health indicator.
3. **SSE framing:** default (unnamed) messages whose body is `{"kind": ..., "data": ...}`. Ledger entries need a stable `_id`; scans should include `correlation_id`.
4. **`courier_position`** x/y in the same 760×420 map coordinates as `locations.map`.
5. **`/transfers` typed body** and the **`confirmation_request`** payload (`readback_text`, optional `estimated_arrival`, `audio_url`) need confirming with Person B.
6. **`/audit/query`** response shape `{answer, mode, entries, scans, requests, gaps}` plus `audio_verified` on request docs if integrity was actually re-checked.
7. **Demo identity:** the switcher sends `user_id` with each call. The backend must authorize from it and reject unauthorized approvals.
8. **Exposure `response`** fields `minutes_out` and `exceeded`; recovery entries should carry `freezer_id` so the map can show the return freezer.
9. **Halting while a read-back or approval is pending.** `POST /transfers/{id}/confirm` (and `/approvals/{id}`) on a halted or finished transfer must return a non-2xx with a readable `detail` (the mock returns `409`), not silently succeed. The halted transfer's `transfer` event should clear `blocked_reason`. The dashboard also times out any pending confirm/approve after 8 s, so a missing response never leaves a button spinning.
10. New requests under a revoked grant should be denied by the gate (a `deny` decision with a rule hit), with no lab call made.
11. **Revoked authority.** There is no grant event on the stream, so the dashboard decides the agent has no authority from ledger evidence: a `halt` or `deny` entry whose `rule_hits` include `grant_revoked`, `grant_expired` or `grant_missing`, until the gate next allows the agent to act. The `stop_switch` rule is deliberately not treated as lost authority. Denials are explained from `rule_hits` (see `src/lib/rules.ts`), so new gate rules should be added there. If grants ever stream (or `GET /state` returns `grants`), that status is used too.
12. **Restoring authority (new).** `POST /grants/{id}/reissue` `{by, reason}`: issues a NEW grant, supervisor only, never the agent, scope copied (never widened), server-set expiry, reason recorded in the ledger. The full contract and security rules for Person A are in `GRANT_REISSUE_SPEC.md`. The dashboard's **Restore authority** button needs it; without it the dialog says so and points at the demo reset.
13. A server-provided simulated clock if `DEMO_SPEED` is used. The browser does not convert time.

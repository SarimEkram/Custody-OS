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
src/lib/         api.ts (only REST module), types.ts, normalize.ts, format.ts, courier.ts, intake.ts, findings.ts, rules.ts, authority.ts, auditLocal.ts, seed.ts
src/store/       custodyStore.ts (Zustand; mirrors backend state, never decides authorization)
```

## Dev-server note (Windows)

If the page behaves as though a file wasn't updated (for example "x is not a function" for something that clearly
exists), Vite's file watcher may have cached a half-written file. Restart it with `npm run dev -- --force`.
A production build (`npm run build`) is not affected.

## Integration status

**How this was checked.** The backend's real route code on `main` (Person B's intake, confirm and stream routes,
Person A's router including `GET /state` and `GET /health`) was run in memory with only the database calls faked
(from the real seed data) and every database address pointed at a dead port, so Atlas was never contacted. This
dashboard was then driven in a real browser against it. Result: **32 of 33 behaviours work.** The one that doesn't
is waiting on the backend (below). What this does *not* cover: the agent loop, the lab services, Atlas itself, and
real change-stream events (those were sent in with the shapes the backend code produces).

| Feature | Status against the real backend |
|---|---|
| Typed request `POST /transfers`, spoken request `POST /voice/requests` | Works (voice verified at the request-format level) |
| Read-back card and `POST /transfers/{id}/confirm` | Works. The sentence comes from the event's `text` |
| Read-back audio (text-to-speech) | Works. Plays the event's `audio_url` (`/api/transfers/{id}/readback/audio`); the text read-back always stands |
| Speak a request (push-to-talk) | Works. A real browser recording was accepted by the real voice route (speech-to-text faked). Not yet tried with a live ElevenLabs key |
| Confirm by voice (agent reads aloud, then listens ~10 s for "confirm") | Built and tested against the mock. **Waiting on the backend** (`POST /transfers/{id}/confirm/voice`, issue 4 in `DASHBOARD_BACKEND_ISSUES.md`). Until then the card says it isn't available and the Confirm button is used |
| Approve / Deny, Stop, Revoke | Work at the route level. Never optimistic: the UI waits for the SSE update |
| "Did you mean...?" and unclear requests | Work. Uses `problems[].suggestions` and `message`, with a fallback for sparse problems |
| Live map and courier dot | Works on the outbound and recovery legs, placed from `progress` (see below) |
| Live ledger, scanner proof, exposure clock | Work. Position and clock change only on scanner events |
| Reload / reconnect resync | Works. `GET /state` restores transfers, activity, scans (so the clock), grants, samples and locations |
| Health indicator | Works. `GET /health` answers 200 even when the database is down, so the dashboard reads `status` and shows "Degraded" |
| Auditor: custody chain, gaps, exposure report | Work. Summary rows are shown as findings. Original requests come from the backend (or `/audit/transfer/{id}`) |
| Audio integrity | Works: "Verify now" calls `/audit/requests/{id}/verify`. The dashboard never claims a hash result itself |
| Refused request (no active grant) | Works. Explained, with Restore authority for the grant holder or a switch for anyone else |
| Gate denial (e.g. `destination_not_certified`) | Works. Plain-language reason and next step from `src/lib/rules.ts` |
| Restore authority | **Waiting on the backend** (`POST /grants/{id}/reissue`) |
| Original-request audio playback | **Waiting on the backend** (`GET /voice/requests/{id}/audio`) |

## Voice

- **Speaking a request:** hold the mic (or Space) and release to send. Capped at 30 s / 1 MB, like the backend. A transcript with
  low-confidence words highlighted, "did you mean" suggestions, and the typed box as a fallback.
- **Confirming by voice:** when a read-back appears the dashboard plays the spoken read-back (`audio_url`), and when it **finishes**
  opens the microphone for **10 seconds**, stopping early after about 1.2 s of silence following speech. The clip is uploaded to
  `POST /transfers/{id}/confirm/voice`; the **backend** transcribes it, decides whether it was a confirmation, and records it. The
  dashboard never decides what was said, and identity is the logged-in user, never the voice.
- **The Confirm button always works** and wins: pressing it at any time stops listening and releases the microphone.
- **It says why when it can't:** blocked microphone, nothing heard (a full 10 s of silence is not uploaded), heard something that
  wasn't a confirmation (shows what it heard, offers *Listen again*), browser blocked autoplay (*tap to start*), no spoken
  read-back (manual *Say "confirm"* button), or the backend lacks the route (clear message, and it stops retrying this session).
- **Settings (this browser only):** the on/off switch on the card (`localStorage` key `custody.voiceConfirm`), and the window length
  `custody.voiceListenSeconds` (3-30, default 10).
- **Needs** Chrome on `localhost` (microphone and `MediaRecorder`).

## What the backend still needs to provide

1. **`POST /grants/{id}/reissue`** `{by, reason}`: see `GRANT_REISSUE_SPEC.md` (supervisor only, never the agent, new
   grant rather than reactivation, scope copied, server-set expiry, ledgered as `grant_reissued`).
2. **`GET /voice/requests/{id}/audio`** to stream the stored recording of a spoken request.
3. **`courier_position` leg start (nice to have).** The event carries `{job_id, transfer_id, sample_id, from, to,
   progress}`. After a "back to origin" reroute the courier service reports `from === to` with progress restarting
   at 0. The dashboard copes by remembering where it last drew the courier, but a page reload mid-recovery loses that.
   Adding the leg's start (e.g. `leg_from`) or `x`/`y` to the event would remove the guesswork.

4. **`POST /transfers/{id}/confirm/voice`** (multipart `audio`, `user_id`) and a spoken "say confirm" cue at the end of the read-back
   text: see issue 4 in `DASHBOARD_BACKEND_ISSUES.md`. The dashboard side is built and waiting.

## Behaviour the dashboard relies on (already true on `main`)

- Confirm / approve on a halted or finished transfer returns a non-2xx with a readable `detail`. The UI also times out
  any pending confirm or approve after 8 s, so a missing reply never leaves a button spinning.
- A request from someone with no active grant is refused with `403` before anything is stored, so no ledger row exists.
  The dashboard treats that refusal, for the person who holds the grant, as "the agent has no authority" (it clears
  when a request is accepted or a grant is re-issued). Only the grant holder (Jordan Lee) can send requests.
- Ledger evidence of lost authority: a `halt` or `deny` entry whose `rule_hits` include `grant_revoked`,
  `grant_expired` or `grant_missing`. `stop_switch` is deliberately not treated as lost authority.
- New gate rules should get a plain-language entry in `src/lib/rules.ts`; unknown rules still display, just less friendly.
- Audit rows: `gaps` rows have only `transfer_id, sample_id, tool, timestamp, on_behalf_of, correlation_id, summary`
  and the exposure report returns per-sample aggregates. Both are shown as findings, not ledger rows.
- `GET /state` carries `demo_speed` (default 60), shown beside the exposure clock as "Demo time x60". The clock itself
  counts real seconds.

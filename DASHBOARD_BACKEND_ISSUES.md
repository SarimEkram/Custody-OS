# Dashboard ↔ backend: open issues

**Raised by:** Person C (dashboard) · **For:** whoever picks these up (suggested owners below) · **Date:** 2026-11-14

> **Update 2026-11-14:** issues 1-3 were fixed on `main` and verified from the dashboard in a real browser against the
> real routes (35 of 35 behaviours). Only issue 4 is open. The sections for 1-3 are kept for reference.

The dashboard is merged on `main` and aligned with the real backend. It was driven in a real browser against the
backend's actual route code (in memory, Atlas never contacted): **32 of 33 behaviours work.** This file covers the
one that doesn't, plus two smaller gaps. Nothing here needs a dashboard change to *start*: the dashboard already
calls these endpoints, shows a clear message while they're missing, and will light up when they exist.

| # | Issue | Suggested owner | Blocks |
|---|---|---|---|
| 1 | ~~`POST /grants/{id}/reissue` does not exist~~ **Done** | Person A (grants, ledger) | The **Restore authority** button |
| 2 | ~~`GET /voice/requests/{id}/audio` does not exist~~ **Done** | Person B (voice, GridFS) | The Auditor's **Play original audio** button |
| 3 | ~~`courier_position` can't place the dot after a reload mid-recovery~~ **Done** (`original_to`, `return_from_progress`) | Person B (courier, events) | Polish only |
| 4 | **Open.** Voice confirmation: `POST /transfers/{id}/confirm/voice` and a spoken "say confirm" cue | Person B (voice, confirmation) | Confirming the read-back by voice |

---

## 1. Restore authority: `POST /grants/{revoked_grant_id}/reissue`

### What happens today
After a grant is revoked the agent can't act, and any request from the grant holder is refused with
`403 "user:jlee has no active grant for the agent"`. The dashboard shows a red banner with a **Restore authority**
button. A supervisor fills in a reason, ticks a box and clicks **Issue new grant**. The backend returns 404, so the
dialog says *"This backend can't issue a new grant yet. For the demo, run scripts/reset_demo.sh and reload."*
The only way back is the reset script.

### What to build
Full contract, error table and ledger entry: **[`dashboard/GRANT_REISSUE_SPEC.md`](dashboard/GRANT_REISSUE_SPEC.md)**.
A working reference implementation of the behaviour is in the mock: search `reissue` in
[`mock-data/server.mjs`](mock-data/server.mjs). Request is `{by, reason}`; success is
`{ok, grant_id, supersedes, expires_at}`.

The security rules are the point of this task. Please keep all of them:

1. **Issue a NEW grant. Never reactivate the revoked one.** The old grant stays `revoked` forever (audit trail,
   append-only story). The new one has `supersedes: "<old id>"`.
2. **Supervisor only.** `by` must be a human with `role == "supervisor"` in `identities`. Technicians, `agent:*`
   identities, and the grantor acting alone are all refused (403). An agent must never be able to grant itself authority.
3. **The server decides the contents.** The client sends only `{by, reason}`. Copy `scope` from the revoked grant
   (never wider), set a short capped expiry, and **ignore** any `scope` / `expires_at` in the body.
4. **Reason required** (min 8 characters), saved in the ledger.
5. **Same grantor and grantee as the old grant.** `find_grant` (`agent/mongo_stores.py`) only matches a grant where
   `grantor == requester`, so the replacement must keep `grantor: "user:jlee"` or the technician still can't send requests.
6. **Write with `admin_svc`**, like `agent/control.py::revoke_grant`. `agent_svc` must not be able to edit grants.
7. **Atomic and not repeatable.** One transaction: insert the new grant, set `superseded_by` on the old one, append the
   ledger row. Re-issuing the same revoked grant a second time returns 409.
8. **Does not resume halted transfers.** They stay `revoked` / `cancelled` / `recovered`.

Things that are fine, so you don't have to worry about them:
- The grant watcher (`agent/watchers.py`) only fires on a `status` change to `revoked` or a delete, so inserting a new
  grant or adding `superseded_by` to the old one will **not** cause a spurious halt.
- The `grants` validator has no `additionalProperties: false`, so `supersedes`, `issued_by`, `superseded_by` are accepted.
  Please add them to `db/validators/grants.json` and `agent/models.py` for documentation.

### Ledger entry
`entry_type: "correction"`, `actor` = the supervisor, `on_behalf_of` = the original grantor, `grant_id` = the **new**
id, `decision: "allow"`, **`rule_hits: ["grant_reissued"]`** (the dashboard keys off this exact string), `reason`,
`response: {supersedes, expires_at}`, a one-sentence `summary`.

**Open decision:** the ledger validator requires `transfer_id`, but this entry isn't about a transfer. Either allow a
placeholder (`"SYSTEM"`, or the most recently halted transfer's id; the mock uses the latest transfer, else `SYSTEM`) or
relax the validator for `correction` entries.

### Acceptance checklist
- [ ] Technician (`user:jlee`) gets 403 "Only a supervisor can issue a new grant."
- [ ] `agent:custody-01` gets 403 "The agent cannot grant itself authority."
- [ ] Reason shorter than 8 characters gets 422.
- [ ] Re-issuing a grant that is still `active` gets 409.
- [ ] Re-issuing the same revoked grant twice gets 409 on the second call.
- [ ] A body containing `scope` or `expires_at` is ignored: the new scope equals the old scope, expiry is server-set.
- [ ] After success: old grant still `revoked`, new grant `active` with `supersedes` set.
- [ ] A ledger row exists with `rule_hits: ["grant_reissued"]` and the supervisor as `actor`.
- [ ] `user:jlee` can now send a request and it passes the gate under the new grant; the halted transfer does not resume.
- [ ] `agent_svc` cannot write to `grants`.

### How to check it end to end
1. Start the backend and `cd dashboard && npm run dev`.
2. As **Jordan Lee**, send a request, then click **Revoke grant**. Send another request: you get the 403 and the
   red banner.
3. Click **Restore authority**, click **Switch demo identity to M. Okafor**, enter a reason, tick the box, **Issue new
   grant**. Expect "New grant issued" with the new id.
4. Click **Switch to Jordan Lee**. The banner is gone and a new request reaches the read-back card.

No dashboard change is needed once the endpoint exists.

---

## 2. Spoken-request audio: `GET /voice/requests/{request_id}/audio`

### What happens today
In the Auditor, a spoken request shows the transcript and a **Play original audio** button. The browser requests
`/api/voice/requests/{id}/audio`, gets 404, and the card shows *"Audio is unavailable or restricted for this request."*
(The **Verify now** button works: `GET /audit/requests/{id}/verify` already exists.)

### What to build
Stream the stored recording:
- Look up `voice_requests[request_id]`, read `audio_file_id`, stream it from the GridFS bucket `voice_audio`. The
  pattern is already in `agent/audit.py::verify_request_audio`, and `agent/mongo_stores.py::MongoAudio.get` reads a clip.
- `Content-Type` from what was recorded (the browser's `MediaRecorder` produces `audio/webm`), plus `Content-Length`.
- `404` with `{"detail": ...}` for an unknown request **or a typed request** (no audio). The dashboard already shows
  "Typed request: there is no audio recording" for typed ones, so it won't normally ask.
- Nice to have: `Accept-Ranges` / Range requests so seeking works.
- Read-only: it must not write to `voice_requests` or GridFS (the audio is evidence).

### Acceptance checklist
- [ ] A spoken request's audio plays from the Auditor **Play original audio** button.
- [ ] A typed or unknown request returns 404 with a readable `detail`.
- [ ] `GET /audit/requests/{id}/verify` still reports `matches: true` afterwards.

No dashboard change is needed.

---

## 3. Courier position after a reload mid-recovery (polish)

### What happens today
`courier_position` (`agent/events.py::CourierTicker`) sends `{job_id, transfer_id, sample_id, from, to, progress}`
and no coordinates. The dashboard turns that into a point on the map. This works on the outbound leg and on the
recovery leg during a normal session.

The catch: after a "back to origin" reroute the courier service sets `destination = origin`
(`services/courier/app.py::reroute`), so the event says `from === to` and `progress` restarts at 0. The dashboard
copes by **remembering where it last drew the courier** and treating that as the start of the return leg. If the
page is reloaded *during* recovery, that memory is gone, so it can't place the dot until recovery ends.

### What to build (small, additive)
When a job is rerouted, remember the original destination and how far it had got, and include them in the event:

```json
{ "job_id": "CJ-208", "transfer_id": "TR-0031", "sample_id": "TS-1077",
  "from": "FZ-A", "to": "FZ-A", "progress": 0.35,
  "original_to": "SEQ-3", "return_from_progress": 0.42 }
```

With `original_to` and `return_from_progress` the dashboard can recompute the start point from the original route
without any memory. Keep the existing fields unchanged.

### Acceptance checklist
- [ ] Existing events are unchanged for jobs that were never rerouted.
- [ ] After a reroute-to-origin, the event also carries `original_to` and `return_from_progress`.

### Dashboard follow-up (Person C)
A few lines in `dashboard/src/lib/courier.ts` (`resolveCourier`) to prefer these fields when present. Ping me when
the event has them.

---

## 4. Voice confirmation: `POST /transfers/{id}/confirm/voice` and the spoken cue

> Optional per the plan (`BUILD_PLAN.md` cut list 2b: "say confirm → button only"), but the dashboard side is built
> and waiting. The button confirm always keeps working.

### The flow the dashboard implements
1. The read-back card appears. The dashboard plays the spoken read-back (`audio_url`, already provided).
2. When the audio **ends**, it opens the microphone and **listens for up to 10 seconds** for the word "confirm"
   (it stops early once it has heard a complete utterance).
3. It uploads that clip to the endpoint below. If the backend says it was a confirmation, the card clears when the
   transfer update arrives, exactly as with the button. If not, it shows what was heard and offers "Listen again".

### What happens today
There is no route that processes a spoken confirmation. `POST /transfers/{id}/confirm` accepts `method: "voice"` but
only records that label; nothing transcribes the audio or decides it was a confirmation. The dashboard therefore
shows *"Voice confirmation isn't available on this backend yet. Use the Confirm button."* and falls back to the button.

Please **don't** reuse `POST /voice/requests` (it would try to start a new transfer from the word "confirm"), and the
dashboard must not decide what was said (the browser never calls ElevenLabs, and the decision is part of the audit trail).

### What to build
**`POST /transfers/{transfer_id}/confirm/voice`**, multipart: `audio` (file), `user_id` (form, `^user:`).

1. Transcribe with the existing `ElevenLabsSTT` (keyterms: just `confirm`). Same limits as intake (1 MB, 30 s).
2. **Decide on the server** whether it is an affirmative confirmation. Suggested: accept "confirm", "yes confirm",
   "confirmed"; **reject anything negated or different** ("don't confirm", "cancel", "no", "confirm nothing"). Keep the
   phrase list in one place with tests; err on the side of *not* confirming.
3. If it matches, run the existing confirm path (`ConfirmationService.confirm`) with `method: "voice"`. Rules unchanged:
   only the requester can confirm (403), a read-back must be pending (409), the transfer must exist (404), identity is the
   logged-in `user_id` and **never** the voice (rule 6).
4. **Keep the evidence.** Store the clip in GridFS and put the transcript and `audio_sha256` in the confirmation ledger
   entry's `response`, next to `readback_text` and `method: "voice"`, so an auditor can hear what was said.
   Do **not** create a new transfer or a request that looks like one.
5. Respond `200 {"confirmed": true, "transcript": "confirm"}` or, when it wasn't a confirmation,
   `200 {"confirmed": false, "transcript": "what was heard", "reason": "not_recognized"}`.
   Errors use `{"detail": ...}`: `413` too large, `422` no speech, `503` speech-to-text down (message
   "Speech-to-text is unavailable; use the Confirm button.").

### Make the agent say the cue
The spoken read-back is the pending read-back text (`SpokenPrompts.readback`), which today ends "...Confirm?".
Please make the text end with an explicit instruction, for example:

> "I'll check out TS-1042 from Freezer B and send it to Sequencing Station 3, arriving about 2:04. **Say “confirm” to go ahead.**"

Put it in the read-back text itself (not only in the audio) so that what is heard is what is shown and what is recorded
in the ledger, consistent with the "what is heard is what will run" rule in `SpokenPrompts`. The dashboard also shows
the cue on screen while listening, so it works with or without audio.

### Acceptance checklist
- [ ] "confirm" (clear speech) returns `confirmed: true`, writes a confirmation ledger entry with `method: "voice"`,
      the transcript and the audio hash, and unblocks the transfer like the button does.
- [ ] "cancel", "don't confirm", "no" return `confirmed: false` and change nothing.
- [ ] A different user than the requester gets 403; nothing pending gets 409; unknown transfer gets 404.
- [ ] Silence or noise returns 422 or `confirmed: false`, never a confirmation.
- [ ] No new `transfers` document and no new intake `voice_requests` doc is created by this call.
- [ ] The spoken and displayed read-back end with the "say confirm" cue.

### How to check it end to end
1. Start the backend with `ELEVENLABS_API_KEY` and `ELEVENLABS_TTS_VOICE_ID`, and `cd dashboard && npm run dev` (Chrome).
2. Send a request (typed or spoken). The read-back is spoken, then the card shows **Listening, say "confirm"** with a
   10-second countdown.
3. Say "confirm": the card clears and the transfer continues. Say "cancel": it shows what it heard and offers
   **Listen again**; the Confirm button still works.

---

## Reference

- Dashboard integration status and the full list of what's verified: [`dashboard/README.md`](dashboard/README.md)
- Contract for issue 1: [`dashboard/GRANT_REISSUE_SPEC.md`](dashboard/GRANT_REISSUE_SPEC.md)
- A mock that implements issue 1 as intended, and mirrors today's real event and response shapes elsewhere (so
  issues 2 and 3 are not modelled): [`mock-data/`](mock-data/) (`node mock-data/server.mjs`, port 8000). It is a
  rehearsal aid, not the gate or the scanner.

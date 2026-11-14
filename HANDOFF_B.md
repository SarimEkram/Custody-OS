# Person B handoff: services, agent loop, voice

Covered by `pytest -q -m "not integration"` (264 tests, no network). Live checks:
`scripts/llm_smoke.py`, `scripts/stt_smoke.py`, `scripts/mongo_smoke.py` (read-only),
`scripts/mongo_store_smoke.py` (scratch database, dropped afterwards).

## Run it

```bash
.venv/bin/python scripts/export_lab_config.py      # .lab_config.json from Atlas (run after every seed/reset)
export SCANNER_URL=http://127.0.0.1:8105 FREEZER_URL=http://127.0.0.1:8101 \
       SCHEDULER_URL=http://127.0.0.1:8102 COURIER_URL=http://127.0.0.1:8103 STATION_URL=http://127.0.0.1:8104
for s in "scanner 8105" "freezer 8101" "scheduler 8102" "courier 8103" "station 8104"; do set -- $s
  .venv/bin/uvicorn --factory services.$1.main:app_factory --port $2 & done
.venv/bin/uvicorn --factory agent.main:create_app --port 8000      # the agent service (+ A's router)
```
Lab systems read `.lab_config.json`, never Atlas. Only the scanner holds a DB user (`scanner_svc`).
`DEMO_SPEED` (default 60) = simulated minutes per real minute; 60 means 1 real second = 1 sim minute.

## For C (dashboard)

- **Typed request:** `POST /api/transfers {user_id, text}` · **voice:** `POST /api/voice/requests`
  (multipart `audio`, `user_id`). Both return `{request_id, transcript, parsed, low_confidence_terms,
  transfer_id, problems}`. `transfer_id` is `null` when the request needs clarification; each entry in
  `problems` has `field`, `reason`, `suggestions` and a plain-language `message` to show or speak
  ("I can't find TS-1024. Did you mean TS-1042?"). 503 from the voice route = speech-to-text is down: focus the typed box.
- **Confirm:** `POST /api/transfers/{id}/confirm {user_id, readback_text, method}` (A's body; B records the
  read-back text it generated, not the one the browser sends, so what was shown is what is logged).
- **SSE:** `GET /api/stream` merges A's Atlas events with B's:
  - `{"kind": "confirmation_request", "data": {"id", "transfer_id", "text"}}`: show the read-back card.
  - `{"kind": "courier_position", "data": {"job_id", "transfer_id", "sample_id", "from", "to", "progress"}}`:
    once a second while a courier is moving; in memory only, never written to Atlas.
- Locations are the seed's ids (`FZ-A`, `SEQ-3`, `FOR-LAB`, `CORR-1`); `progress` runs 0 to 1 from `from` to `to`.

## For A

- Every tool call goes through `AtlasExecutor` -> `policy_gate.execute` (`agent/atlas.py`). B never calls a lab
  system any other way. The loop persists `reservation_id`/`courier_job_id` on the transfer (your checkout
  preconditions read them) and sets `reserved`/`recovering`/`cancelled`; verified states come only from scans.
- **`Watchers.on_scan` must stay `ScanApplier.handle`** (it is the only consumer): applying a scan twice would
  write a false `mismatch`. B waits on the applied result, not on the raw event.
- `on_approval` -> `signals.signal("approval:<id>", status)` wakes the paused loop; `on_halt` -> `runner.halt`,
  which cancels the run even mid-tool (e.g. `deliver` waiting for the courier) and runs recovery once.
- B's `/stream` and `/transfers/{id}/confirm` are registered before your router and are supersets of yours
  (FastAPI warns about duplicate operation ids; that is expected). `confirm_readback` is still what records it.
- `ledger.append(entry, session=...)` is used as you defined it. B writes only intake rows itself (via `append`).
- Recovery runs with `RunContext(mode="recovery", on_behalf_of=<revoker>)`; B does not write exposure rows.

## Replay without a model

`DEMO_SCRIPTED=1 DEMO_SCRIPT=demo/script.json`: the loop, gate, lab and scanner stay real; only the model is replayed
(`agent/scripted.py`; `RecordingLLM` saves a good live run). One script is one stream of replies across runs.

## Seed fix: scanner reader locations

`db/seed.py` built each reader's `location_id` with `suffix.upper()`, so `scanner:reader-fza` sat at `FZA`, a location
that does not exist (real ids: `FZ-A`, `FZ-B`, `CORR-1`, `SEQ-3`, `FOR-LAB`). The scanner rejects a scan whose reader is
not at the location it reports, so this would have refused every real scan. Fixed in the seed (explicit reader ->
location map; `tests/test_seed_data.py` guards it) and applied to the live cluster for those five documents. Reader ids
did not change. After `python -m db.seed --reset` the data is correct; `export_lab_config.py` should report no
unresolved readers. `services/config.py` still falls back to a hyphen-insensitive match for any stray reader and
lists the ones it cannot place.

## Known gaps

- The requested window stays text ("2 PM"); nothing schedules by time yet.
- No resume token on the scan feed or crash-resume of an interrupted transfer (stretch items).
- Text-to-speech read-backs not built (stretch). Groq's free tier is rate-limited (~30 req/min, 8k tokens/min).

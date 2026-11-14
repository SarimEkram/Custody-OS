# Hackathon context

**Event:** MongoDB "Agents in Context" Toronto Hackathon (2026-11-14, one-day build).
**Our track:** Track 1, **Agents You Can Audit**.

## Track 1 brief (verbatim)

> An agent that can't explain what it did, and who allowed it, never leaves the sandbox. Build an agent using MongoDB Atlas as the identity, policy and audit layer (collections, schema validation, change streams) that takes real actions across systems, where every action is tied to an identity, checked against policy before it runs, and recorded so a reviewer can reconstruct it afterward.
>
> - How do you decide what an agent may do and enforce it before the action, not after?
> - How do you record what the agent did, why, and under whose authority?
> - How do you stop or roll back an agent mid-run?
>
> Examples: a claims-handling agent with per-step policy gates and a ledger a compliance reviewer can query; a KYC-refresh agent that is blocked from touching accounts outside its scope.

The other two tracks (for context only): **Memory You Can Govern** (scoped, expiring, correctable memory with Vector Search) and **Finish the Job** (long multi-step tasks that survive crashes, using verified task state). We borrow one idea from Track 3: telling an action that was *sent* from one that *took effect*.

## Our answer to the three questions

| Track 1 question | Our answer | Where it lives |
|---|---|---|
| Decide and enforce **before** the action | Requests are spoken (ElevenLabs speech-to-text), but the voice grants nothing: authority comes from the logged-in identity and its grant. A policy gate wraps every tool call. It checks the grant (valid, in scope, not expired or revoked), the sample's restriction level, the destination's certification, the time window, and similar past incidents (Vector Search). Denied calls never reach a lab system; they are logged and become an approval request. | `agent/policy_gate.py`, `grants`, `policies` |
| Record what, why, and under whose authority | The original audio (GridFS, hashed), transcript and speaker are stored, and every ledger entry links back to that request. Append-only `ledger` with schema validation and an insert-only DB role. Each entry has the actor, the human it acted for, the grant, the policy version, the agent's stated reason, the request/response, and the independent verification result. An aggregation rebuilds any sample's custody chain. | `ledger`, `custody_events`, `DATA_MODEL.md` |
| Stop or roll back mid-run | A change stream on `grants` (and a Stop switch) halts the agent at its next step. Reversible actions are compensated (cancel reservation, retract notification). Before the first irreversible step, the agent reads the plan back and waits for the tech's confirmation. Irreversible ones (sample out of the freezer) trigger a recovery plan: return to the nearest certified freezer before the exposure clock runs out, and log the exposure. | `agent/recovery.py`, `ARCHITECTURE.md` |

## Judging rubric and how we score

| Criterion | Weight | What judges look for | How we win it |
|---|---|---|---|
| **Technical demo** | 35% | Impressive, well-engineered, working | A spoken request kicks off the run, with a read-back before the sample leaves the freezer. Live lab-floor map with a sample moving; a transfer blocked live; a revocation mid-route with a countdown and recovery; an auditor query that rebuilds the whole chain. Rehearsed, with a reset script and fallbacks (`DEMO.md`). |
| **Implementation difficulty** | 30% | More than a few AI prompts away | Real multi-service system: voice intake with confidence-aware parsing and DB validation, custody state machine, pre-action gate, independent verifier with a separate DB identity, change-stream-driven interruption, transactional handoffs, compensation vs. recovery planning, Vector Search for risk and audit. |
| **Creativity** | 15% | Something never seen before | Chain of custody is literally an audit problem; "agent's word doesn't count until the scanner agrees" and "you can't undo a physical move" are fresh angles. |
| **Impact potential** | 20% | Real-world pain point | Lab techs wear gloves and handle samples, so typing is a real friction; voice fits the bench. Broken custody gets forensic evidence thrown out of court; clinical/biobank samples are irreplaceable and regulated (ISO 15189, FDA 21 CFR Part 11). Today it's paper and spreadsheets. |

## Pitch (30-second version)

> Chain of custody decides whether a DNA swab holds up in court. Today it's clipboards and signatures. Labs want automation, but an AI agent moving evidence is exactly the "who allowed this?" problem. With Custody Agent, a gloved tech just says what needs to move. The agent can't act on its own say-so, or on a voice alone: every move is checked against policy before it happens, confirmed by an independent scanner, and written to a ledger it can't edit. Revoke its authority mid-transfer and it stops, undoes what it can, and gets the sample back into a freezer before it spoils, and the auditor can see every step.

**Lead framing:** forensic evidence (clearest stakes). **Second market:** clinical labs and biobanks, environmental testing.

## Other tech

- **ElevenLabs** speech-to-text (`scribe_v3`) for spoken requests; optional text-to-speech for spoken read-backs.
- **Groq** free API (OpenAI-compatible; default model `openai/gpt-oss-120b`) for planning and tool use.
- **Voyage AI** embeddings for Vector Search, through Atlas (Automated Embedding or an AI Model APIs key).

## MongoDB features we must visibly use

Collections, GridFS (original audio), `$jsonSchema` validation, roles, change streams, multi-document transactions, TTL indexes, Atlas Vector Search, aggregation pipelines. Each one should appear at least once in the demo or the slides. See the mapping in `DATA_MODEL.md`.

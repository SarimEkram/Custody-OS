# CustodyOS — Build the Functional React + TypeScript Frontend

You are a senior frontend engineer, product designer, and systems integration engineer working on **CustodyOS**, an auditable AI agent for laboratory and forensic evidence chain-of-custody workflows.

Your task is to **implement a complete, functional, polished frontend** inside the existing repository, using React, TypeScript, Vite, and Tailwind CSS.

This is a time-constrained MongoDB hackathon project. Prioritize a genuinely working end-to-end demonstration over unnecessary abstractions or decorative features.

**Do not simply generate a visual mockup. Implement the actual application, components, state management, API integration, event handling, error states, and demo interactions.**

## 1. Read the Project Documentation First

Before modifying code, read:

- `CLAUDE.md`
- `docs/ARCHITECTURE.md`
- `docs/BUILD_PLAN.md`
- `docs/DATA_MODEL.md`
- `docs/DEMO.md`
- `docs/HACKATHON.md`
- `docs/TEAM.md`

If these documents are stored elsewhere in the repository, locate them.

These documents are the source of truth.

Pay particular attention to:

1. Existing REST endpoints and SSE event contracts.
2. Transfer states and permitted transitions.
3. MongoDB document shapes.
4. Voice intake and read-back confirmation.
5. Independent scanner verification.
6. Supervisor approvals.
7. Stop, grant revocation, and recovery.
8. The exact demonstration sequence.
9. Seed data and sample IDs.

Do not rename existing endpoints, domain objects, statuses, or identifiers merely to fit your preferred implementation.

Inspect the existing code before creating new files. Reuse functioning components and utilities.

If a documented endpoint has not been implemented, build a typed adapter around its expected contract. Clearly identify missing backend functionality rather than pretending it works.

Do not modify the backend unless a minimal, safe integration fix is essential. Prefer to document backend requirements for the responsible teammate.

---

# 2. Product Vision

CustodyOS should feel like a premium, professional mission-control platform for autonomous agents.

Visual inspiration:

- Linear: restrained, polished interface and typography.
- Modern security operations centers: live events and clear status indicators.
- High-end logistics dashboards: location visualization and operational clarity.
- Developer observability tools: detailed evidence inspection and event timelines.

Avoid:

- Generic chatbot layouts.
- Oversized cards and excessive whitespace.
- Excessive gradients, glassmorphism, or neon effects.
- Unnecessary animations.
- Tiny text.
- Dense enterprise tables on the main dashboard.
- Fake interactions that appear functional.
- Decorative controls without meaningful actions.

The interface should be understandable within five seconds of opening it.

The core user experience:

**Speak a request → Review interpretation → Confirm → Watch the agent work → Approve or Stop when needed → Inspect the evidence.**

Everything should happen without unnecessary navigation.

---

# 3. Visual Design System

Use a sophisticated dark theme by default.

Colors:

- Primary background: `#0B1220`
- Secondary surfaces: `#111D2E`
- Elevated panels: `#16243A`
- Borders: `#29384D`
- Primary text: `#F1F5F9`
- Secondary text: `#9CAFC5`
- Authorized/success: `#59E1C1`
- Pending/approval: `#F2B86B`
- Denied/revoked: `#F06B78`
- Information: `#7AA9F8`

Use Inter or Geist for UI text and a monospaced font for technical identifiers.

Use:

- Tailwind CSS
- Lucide React icons
- Accessible button, dialog, tooltip, and dropdown components
- Consistent 8px spacing rhythm
- Subtle shadows and thin borders
- Smooth 150–250ms transitions

Status colors must be accompanied by labels or icons.

Ensure the UI remains legible on a projector.

---

# 4. Application Layout

Build a responsive single-page application with three views:

1. Control Center
2. Auditor
3. Demo & Settings

The **Control Center** is the primary experience.

## Desktop layout

Use the layout specified in `DEMO.md`:

- Top navigation/header across the full width.
- Left 60%: interactive laboratory floor map and workflow visualization.
- Right 40%: live ledger, approvals, confirmation, and workflow details.

The top bar must include:

- CustodyOS branding.
- Current authenticated/demo identity.
- Prominent push-to-talk control.
- Transcript/typed command entry.
- SSE connection indicator.
- Current agent status.
- Access to Auditor and Demo views.

The active workflow must always be visible.

The Stop control must remain accessible whenever the agent is operating.

## Responsive behavior

For narrower screens:

- Collapse secondary navigation.
- Stack the map above the ledger.
- Keep command entry accessible.
- Use drawers for detailed audit evidence.
- Preserve access to Stop and approvals.

Optimize primarily for a 1440px desktop presentation.

---

# 5. Interactive Laboratory Floor Map

This is the visual centerpiece of the application.

Build a custom SVG-based laboratory floor map using the locations defined in the seed data.

Required locations:

- `FZ-A` — Freezer A
- `FZ-B` — Freezer B
- `CORR-1` — Corridor
- `SEQ-3` — Sequencing Station 3
- `FOR-LAB` — Forensics Lab

Use coordinates from the backend location records when available.

Display:

- Distinct rooms and zones.
- Storage locations.
- Route connections.
- Sample identifiers.
- Courier position.
- Current destination.
- Active transfer route.
- Policy checkpoints.
- Scanner verification points.

## Map interaction

Clicking a sample should show:

- Sample ID.
- Type.
- Restriction level.
- Current verified location.
- Custodian.
- Current transfer.
- Exposure limit.

Clicking a location should show:

- Name.
- ID.
- Certifications.
- Samples currently present.
- Relevant restrictions.

Clicking an active route or checkpoint should open the associated workflow or ledger evidence.

## Map animation

Courier movement must be driven by backend SSE `courier_position` events.

Do not independently simulate movement when real backend data is expected.

Distinguish:

- Planned route: muted dashed line.
- Authorized route: mint.
- Pending approval: amber.
- Revoked: coral.
- Recovery route: amber with directional indicator.
- Completed and independently verified handoff: solid mint.

Show a small moving courier marker during active transit.

When the grant is revoked, the planned destination route should be replaced by the recovery route once the backend reports that recovery has begun.

**Critical:** A service reporting that an action was sent is not proof that the physical handoff occurred. Do not change the verified specimen location until a matching scanner event is received.

Use `custody_events` as the source of physical custody verification.

---

# 6. Voice-First Command Center

Implement a prominent push-to-talk microphone interface.

Use the browser's `MediaRecorder` API.

Required behavior:

1. User presses and holds the microphone.
2. Recording begins.
3. Show an animated recording indicator and elapsed duration.
4. Release ends recording.
5. Upload the audio using `POST /api/voice/requests`.
6. Display transcription processing state.
7. Display the returned transcript.
8. Show parsed request fields.
9. Allow correction or clarification when necessary.

Use the documented request contract:

`POST /voice/requests`

Multipart fields:

- `audio`
- `user_id`

Expected response:

- `request_id`
- `transcript`
- `parsed`
- `low_confidence_terms`
- `transfer_id`

The Vite proxy should map `/api` to the FastAPI backend at `localhost:8000`.

Do not expose ElevenLabs credentials in frontend code.

## Voice interaction states

Implement:

- Idle
- Recording
- Uploading
- Transcribing
- Transcript ready
- Needs clarification
- Error

Display useful error messages for:

- Microphone permission denied.
- Unsupported browser.
- Recording failure.
- Network failure.
- Transcription failure.
- Unknown sample ID.
- Ambiguous destination.

Use accessible keyboard and pointer interactions.

## Transcript UI

Display the transcript in an editable panel.

Highlight low-confidence terms when provided by the backend.

For example:

"Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM."

If the backend cannot confidently identify a sample or location, present the backend-provided alternatives and require clarification.

Never silently guess an identifier.

## Typed fallback

A typed request box must always be available.

Use:

`POST /api/transfers`

Typed requests must use the same downstream workflow and authorization checks as voice requests.

The frontend must not create a separate, less-restricted execution path.

## Optional TTS

If the backend supports read-back audio, show a speaker button.

Do not make TTS a dependency for successful workflow execution.

The text read-back must always work.

---

# 7. Read-Back Confirmation Card

Before the first irreversible operation, display a clear confirmation card.

Example:

**Confirm Transfer**

Sample: TS-1042  
From: Freezer B  
To: Sequencing Station 3  
Requested arrival: 2:00 PM  
Estimated arrival: 2:04 PM

"I'll check out TS-1042 from Freezer B and send it to Sequencing Station 3. Confirm?"

Buttons:

- Confirm Transfer
- Cancel / Review

Use:

`POST /api/transfers/{id}/confirm`

Do not execute irreversible actions from the frontend.

The confirmation only informs the backend that the user accepted the read-back.

Show a pending state until the backend acknowledges confirmation.

If the transfer is blocked with `blocked_reason: "readback_pending"`, surface this card automatically.

The card should be visually prominent but should not obscure the entire dashboard.

---

# 8. Live Audit Ledger

Build a real-time event stream showing the agent's activity.

Use:

`GET /api/stream`

with the browser's `EventSource` API.

The documented event envelope is:

```ts
type StreamEvent = {
  kind:
    | "ledger"
    | "transfer"
    | "scan"
    | "approval"
    | "confirmation_request"
    | "courier_position";
  data: unknown;
};
```

Create a strongly typed normalization layer for the payloads.

Do not invent fields that the backend does not provide.

## Ledger design

Each row should display:

- Timestamp.
- Actor.
- Action/tool.
- Policy decision.
- Status.
- Sample or transfer reference.
- Brief explanation.

Support statuses:

- ALLOWED
- DENIED
- PENDING APPROVAL
- PENDING CONFIRMATION
- HALTED
- SENT
- CONFIRMED
- MISMATCH
- UNCONFIRMED

Make the distinction between `SENT` and `CONFIRMED` especially clear.

A sent action should not automatically become green/verified.

Only show confirmed when verification evidence is received.

## Ledger interactions

Clicking a row opens a detailed drawer containing:

- Actor.
- On-behalf-of identity.
- Grant ID.
- Policy ID and version.
- Agent's stated reason.
- Rule hits.
- Correlation ID.
- Request ID.
- Action arguments.
- Execution outcome.
- Independent scanner evidence, if available.
- Timestamp.

Show the causal relationship:

**Policy decision → Tool execution → Scanner verification**

Support filtering by:

- Sample.
- Transfer.
- Actor.
- Decision.
- Verification status.

Keep the main ledger concise, with advanced technical details hidden until requested.

## SSE reliability

Implement a reusable `useEventStream()` hook.

Requirements:

- Establish one connection.
- Clean up on unmount.
- Display connection status.
- Handle disconnects.
- Reconnect automatically.
- Deduplicate events by stable IDs.
- Avoid duplicate entries after reconnection.
- Fetch authoritative state after reconnection when supported.
- Avoid excessive rerenders.

Do not generate fake ledger entries during normal connected operation.

---

# 9. Approval Inbox and Identity Switcher

Implement a supervisor approval panel.

The demo identities are:

- Jordan Lee — `user:jlee` — Lab technician
- M. Okafor — `user:mokafor` — Supervisor

Provide an identity switcher in the top navigation.

For the hackathon, this can use a backend-supported demo identity mechanism.

Clearly label this as **Demo Identity**.

Do not represent a client-side dropdown as production authentication.

## Approval card

Display:

- Approval ID.
- Sample.
- Requested action.
- Reason approval is required.
- Required role.
- Requesting identity.
- Expiration time.
- Current status.

Actions:

- Approve
- Deny

Use:

`POST /api/approvals/{id}`

Only enable supervisor actions when the active identity has the required authority.

Backend authorization remains mandatory.

When an approval is submitted:

1. Show pending state.
2. Wait for server acknowledgment.
3. Update the approval status.
4. Listen for the resumed workflow through SSE.
5. Show the corresponding audit entry.

Do not optimistically mark a protected action as approved before backend confirmation.

---

# 10. Stop, Revocation, and Recovery

This is the most important live demonstration feature.

Implement a persistent **STOP AGENT** button.

Use:

`POST /api/control/stop`

Also support grant revocation through:

`POST /api/grants/{id}/revoke`

The UI must distinguish:

- Stop requested.
- Halt acknowledged.
- Recovery in progress.
- Recovery completed.
- Recovery failed / human intervention required.

Do not claim that an in-flight HTTP action was instantly cancelled.

## Stop interaction

When the user clicks Stop:

1. Submit the stop request.
2. Show "Stop requested".
3. Wait for the backend state/event.
4. Display "Authority revoked" or the confirmed halt state.
5. Show the recovery process when necessary.

For the demo, the Stop button should be large and visually unmistakable.

## Recovery visualization

When a sample is already checked out:

- Show the recovery state.
- Change the route visualization.
- Display the selected return freezer.
- Show reversible compensations.
- Show the countdown.
- Wait for independent check-in confirmation.
- Display the final exposure outcome.

Use the documented transfer states:

```ts
type TransferState =
  | "requested"
  | "reserved"
  | "checked_out"
  | "in_transit"
  | "received"
  | "blocked"
  | "revoked"
  | "recovering"
  | "recovered"
  | "cancelled";
```

Make the difference between rollback and recovery visually obvious.

Reservations can be cancelled.

A specimen already outside storage requires a verified recovery operation.

---

# 11. Specimen Exposure Countdown

Build a countdown that activates after verified freezer checkout.

Calculate it using:

- `samples.checked_out_at`
- `samples.max_minutes_out`

Use the backend's UTC timestamps.

Do not start the countdown merely because the agent submitted a checkout request.

Display:

- Time remaining.
- Maximum allowed exposure.
- Current specimen.
- Exposure status.

Use three visual states:

- Safe: mint.
- Approaching limit: amber.
- Exceeded limit: coral.

The countdown should continue during recovery.

If the limit is exceeded, show the backend-reported exposure violation.

For demo mode, support the documented `DEMO_SPEED` time compression without changing the actual production timing assumptions.

Use a server-provided simulated clock or time scale when available; do not invent a conversion in the browser.

---

# 12. Auditor View

Create a dedicated auditor interface.

It should feel like a forensic investigation tool.

Layout:

- Search and filters at the top.
- Search results on the left.
- Detailed chain-of-custody timeline in the center.
- Evidence inspector on the right or in a drawer.

Use:

`POST /api/audit/query`

Support natural-language questions when the backend supports them.

Examples:

- "What happened to TS-1077?"
- "Show restricted transfers without supervisor approval."
- "Find custody gaps."
- "Show every action performed under GRANT-0007."

Provide predefined query chips for the live demo.

## Chain-of-custody reconstruction

For a selected sample, display:

1. Original request.
2. Authenticated speaker/requester.
3. Transcript.
4. Read-back confirmation.
5. Policy decisions.
6. Lab service actions.
7. Scanner confirmations.
8. Custody handoffs.
9. Revocation events.
10. Compensations and recovery.
11. Final exposure status.

Highlight missing verification events.

## Original audio playback

If the request originated from voice and the backend provides an authorized audio retrieval endpoint:

- Show a play/pause button.
- Display the transcript.
- Show the audio SHA-256 hash.
- Display verification status when provided.
- Handle missing or restricted audio gracefully.

Never expose GridFS credentials or connect directly to MongoDB from the browser.

Do not claim audio integrity was verified unless the backend actually performed the check.

---

# 13. Demo Director

Implement a compact demo control panel under Demo & Settings.

This is an important hackathon feature.

It should make the entire demonstration repeatable and easy to rehearse.

Provide three scenario launchers:

### Scenario 1 — Normal Transfer

Sample: `TS-1042`

Source: Freezer B  
Destination: Sequencing Station 3

Expected:

- Voice or typed intake.
- Policy checks.
- Reservation and courier assignment.
- Read-back.
- Checkout.
- Scanner confirmation.
- Courier movement.
- Intake confirmation.
- Received state.

### Scenario 2 — Restricted Evidence

Sample: `EV-2201`

Destination: Forensics Lab

Expected:

- Request initiated by Jordan Lee.
- Forensic restriction detected.
- Supervisor approval required.
- Agent pauses.
- Identity switched to M. Okafor.
- Supervisor approves.
- Workflow resumes.
- Scanner confirms custody actions.

### Scenario 3 — Mid-Transfer Revocation

Sample: `TS-1077`

Source: Freezer A  
Destination: Sequencing Station 3

Expected:

- Transfer starts.
- Sample is checked out.
- Courier begins moving.
- Exposure countdown starts.
- Grant `GRANT-0007` is revoked.
- Agent halts.
- Reversible actions are compensated.
- Recovery route is displayed.
- Scanner confirms freezer check-in.
- Exposure outcome is recorded.

## Demo controls

Provide:

- Scenario selection.
- Reset Demo.
- Backend health indicator.
- SSE status.
- Current demo identity.
- Scripted-mode indicator, if enabled.
- Links or instructions for backup assets.

Only wire controls to existing backend endpoints or scripts that can be safely invoked through the backend.

Do not let the frontend directly execute shell commands.

If reset is not exposed through a safe backend endpoint, display the documented reset command instead.

Do not silently implement destructive database reset behavior in the frontend.

---

# 14. API Layer and Types

Create:

`dashboard/src/lib/api.ts`

All REST requests must pass through this module.

Create:

`dashboard/src/lib/types.ts`

Mirror the Pydantic models and domain shapes from `DATA_MODEL.md`.

Use strict TypeScript.

Avoid `any` where possible.

Handle:

- Loading states.
- HTTP errors.
- Network failures.
- Invalid responses.
- Backend unavailability.
- Expired permissions.
- Pending approvals.
- Unknown IDs.

Use an environment-configurable API base URL, defaulting to `/api`.

Configure Vite to proxy `/api` to `http://localhost:8000`.

Ensure path rewriting matches the actual FastAPI routes.

Do not duplicate API request logic across components.

---

# 15. Application State

Use Zustand or a small React reducer.

State should include:

- Active identity.
- Active transfer.
- Selected sample.
- Locations.
- Courier position.
- Transfer status.
- Ledger events.
- Scanner confirmations.
- Pending approvals.
- Read-back request.
- Voice recording state.
- SSE connection status.
- Auditor selection.
- Demo scenario.

The backend is the source of truth for workflow and authorization state.

Do not allow frontend state to override a backend policy decision.

Use optimistic UI only for harmless interactions, such as opening panels or changing filters.

---

# 16. Suggested Component Architecture

Use a structure similar to:

```text
dashboard/
├── src/
│   ├── app/
│   │   ├── App.tsx
│   │   └── AppShell.tsx
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Header.tsx
│   │   │   ├── Navigation.tsx
│   │   │   └── IdentitySwitcher.tsx
│   │   ├── map/
│   │   │   ├── LabMap.tsx
│   │   │   ├── LocationNode.tsx
│   │   │   ├── CourierMarker.tsx
│   │   │   └── RouteOverlay.tsx
│   │   ├── command/
│   │   │   ├── MicButton.tsx
│   │   │   ├── TranscriptEditor.tsx
│   │   │   ├── TypedRequest.tsx
│   │   │   └── ReadbackCard.tsx
│   │   ├── workflow/
│   │   │   ├── WorkflowStatus.tsx
│   │   │   ├── ExposureCountdown.tsx
│   │   │   ├── StopAgentButton.tsx
│   │   │   └── GrantCard.tsx
│   │   ├── ledger/
│   │   │   ├── LedgerStream.tsx
│   │   │   ├── LedgerEvent.tsx
│   │   │   └── EvidenceDrawer.tsx
│   │   ├── approvals/
│   │   │   └── ApprovalInbox.tsx
│   │   ├── auditor/
│   │   │   ├── AuditorPanel.tsx
│   │   │   ├── CustodyTimeline.tsx
│   │   │   └── AudioEvidencePlayer.tsx
│   │   └── demo/
│   │       └── DemoDirector.tsx
│   ├── hooks/
│   │   ├── useEventStream.ts
│   │   └── useMediaRecorder.ts
│   ├── lib/
│   │   ├── api.ts
│   │   ├── types.ts
│   │   └── formatters.ts
│   ├── store/
│   │   └── custodyStore.ts
│   ├── pages/
│   │   ├── ControlCenter.tsx
│   │   ├── AuditorPage.tsx
│   │   └── DemoPage.tsx
│   └── styles/
│       └── globals.css
```

This is a suggested structure, not a requirement to rewrite existing working code.

---

# 17. Demo Reliability

This project will be demonstrated live at a hackathon.

Reliability is more important than advanced visual effects.

Implement:

- Clear backend disconnected state.
- Clear SSE reconnecting state.
- No uncaught exceptions in normal workflows.
- No blank screen when the backend is unavailable.
- Typed fallback when voice fails.
- Graceful handling of missing audio.
- Deterministic seed data display.
- Resettable demo state when supported.
- Meaningful loading indicators.
- No duplicated audit events.
- No fake success messages.

Support the backend's `DEMO_SCRIPTED=1` mode if available.

The UI should clearly distinguish scripted demonstration mode from live AI execution.

Never fabricate scanner confirmations or audit records.

---

# 18. Testing and Verification

After implementation, run the available checks:

```bash
npm install
npm run build
npm run lint
```

Fix TypeScript and build errors.

If the repository already contains frontend tests, run them.

Add focused tests for:

1. Transfer state rendering.
2. Ledger event normalization.
3. SENT vs CONFIRMED status.
4. Approval permissions.
5. Stop request states.
6. Countdown calculation.
7. SSE event deduplication.
8. Voice fallback behavior.

Where practical, test the actual backend endpoints.

If the backend is not running, do not claim integration tests passed.

Document which features are:

- Fully integrated.
- Implemented but awaiting backend.
- Demo-only.
- Not yet implemented.

---

# 19. Implementation Priority

Follow this exact priority order.

## P0 — Must work

1. Vite + React + TypeScript app.
2. Professional responsive dashboard layout.
3. API layer and shared types.
4. SSE connection and live ledger.
5. Lab map displaying real transfer state.
6. Typed request submission.
7. Read-back confirmation.
8. Supervisor approvals.
9. Stop and revocation.
10. Exposure countdown.
11. Functional error/loading states.

## P1 — High impact

12. MediaRecorder push-to-talk.
13. ElevenLabs backend transcription integration.
14. Courier position animation.
15. Auditor timeline.
16. Scanner verification evidence.
17. Demo Director and reset integration.

## P2 — Only after core functionality works

18. Original audio playback.
19. Optional text-to-speech.
20. Advanced filtering.
21. Additional microanimations.
22. Extra visual polish.

Do not sacrifice working P0 features for P2 enhancements.

---

# 20. Final Deliverables

When finished, provide:

1. A working React + TypeScript Vite application.
2. Clean, responsive UI.
3. Functional REST integration.
4. SSE-driven updates.
5. Functional typed request workflow.
6. Push-to-talk recording and upload.
7. Read-back confirmation.
8. Approval and Stop interactions.
9. Lab map and transfer visualization.
10. Live audit ledger.
11. Auditor view.
12. Demo controls.
13. Successful production build.
14. Concise setup instructions.
15. A list of missing backend dependencies, if any.

Also provide a short summary of the files created or modified.

---

# 21. Acceptance Criteria

The implementation is complete when:

- The application loads without errors.
- The main dashboard is usable at 1440px width.
- The user can submit a typed transfer request.
- The user can record and upload a voice request when the backend is available.
- The transcript and parsed request are displayed.
- Read-back confirmation works.
- The ledger receives live events through SSE.
- The map updates based on transfer and courier events.
- SENT and CONFIRMED are visibly different.
- A supervisor can approve a restricted transfer through the backend.
- Stop and revocation send real requests.
- Recovery state is shown accurately.
- The exposure countdown follows verified checkout.
- The auditor can inspect linked evidence.
- The demo can be reset through the documented mechanism.
- Backend failures are handled gracefully.
- `npm run build` passes.
- No production UI falsely claims an unverified action succeeded.

**Final instruction:**

Start by inspecting the existing repository and documentation. Then implement the application directly. Make sensible design decisions without repeatedly asking for confirmation. Preserve existing backend contracts and keep the end-to-end demo path working throughout development.

This is not a conceptual design exercise. Build the functional CustodyOS frontend.
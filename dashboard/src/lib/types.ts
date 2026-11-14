// Mirrors agent/models.py (Pydantic) and DATA_MODEL.md.
// Dates arrive over the wire as ISO-8601 strings (UTC).

export type TransferState =
  | 'requested'
  | 'reserved'
  | 'checked_out'
  | 'in_transit'
  | 'received'
  | 'blocked'
  | 'revoked'
  | 'recovering'
  | 'recovered'
  | 'cancelled'

export const TERMINAL_STATES: readonly TransferState[] = ['received', 'recovered', 'cancelled']

export type BlockedReason = 'approval_pending' | 'readback_pending'

export type Decision = 'allow' | 'deny' | 'pending_approval' | 'pending_confirmation' | 'halt'

export type EntryType =
  | 'intake'
  | 'confirmation'
  | 'decision'
  | 'outcome'
  | 'verification'
  | 'compensation'
  | 'recovery'
  | 'exposure'
  | 'violation'
  | 'correction'

export type OutcomeStatus = 'sent' | 'confirmed' | 'mismatch' | 'unconfirmed'
export type Restriction = 'standard' | 'controlled' | 'forensic'
export type ScanType = 'checkout' | 'handoff' | 'intake' | 'checkin' | 'seal_check'

export interface Identity {
  _id: string
  name: string
  role: 'lab_tech' | 'supervisor'
  roleLabel: string
  clearances: Restriction[]
}

export interface LabLocation {
  _id: string
  name: string
  kind: 'freezer' | 'station' | 'corridor' | 'lab'
  certifications: string[]
  map: { x: number; y: number }
}

export interface Sample {
  _id: string
  type: 'tissue' | 'evidence'
  description: string
  restriction: Restriction
  location_id: string
  custodian?: string
  status?: 'stored' | 'out' | 'compromised' | 'consumed'
  max_minutes_out: number
  checked_out_at?: string | null
}

export interface Grant {
  _id: string
  grantor: string
  grantee: string
  status: 'active' | 'revoked'
  expires_at?: string
  revoked_by?: string | null
  /** The revoked grant this one replaces (a re-issue never reactivates the old grant). */
  supersedes?: string | null
}

export interface ReissueResult {
  ok: boolean
  grant_id: string
  supersedes: string
  expires_at: string
}

export interface Transfer {
  _id: string
  sample_id: string
  from: string
  to: string
  requested_by: string
  grant_id: string
  request_id?: string
  source?: 'voice' | 'typed'
  readback_confirmed?: boolean
  blocked_reason?: BlockedReason | null
  state: TransferState
  halt?: boolean
  updated_at?: string
}

export interface LedgerEntry {
  _id: string
  timestamp: string
  entry_type: EntryType
  transfer_id: string
  sample_id?: string | null
  request_id?: string | null
  correlation_id: string
  actor: string
  on_behalf_of: string
  grant_id?: string | null
  policy_id: string
  policy_version: number
  tool?: string | null
  args?: Record<string, unknown> | null
  reversible?: boolean | null
  reason?: string | null
  decision: Decision
  rule_hits?: string[]
  risk_flags?: string[]
  request?: unknown
  response?: Record<string, unknown> | null
  status?: OutcomeStatus | null
  refs?: string[]
  summary?: string
  // `embedding` is never sent to the browser.
}

export interface ScanEvent {
  _id?: string
  scanner_id?: string
  type: ScanType
  sample_id: string
  location_id: string
  from_custodian?: string
  to_custodian?: string
  correlation_id?: string
  seal_id?: string
  scanned_at: string
  signature?: string
}

export interface Approval {
  _id: string
  transfer_id: string
  sample_id: string
  requested_for_tool: string
  reason_requested: string
  approver_role?: string
  requested_by?: string
  status: 'pending' | 'approved' | 'denied'
  decided_by?: string | null
  expires_at?: string
}

export interface ParsedRequest {
  sample_id: string | null
  from: string | null
  to: string | null
  window_start?: string | null
}

// POST /voice/requests and POST /transfers return the same shape.
// `alternatives` is an optional backend extension for "did you mean" prompts.
export interface IntakeResponse {
  request_id: string
  transcript: string
  parsed: ParsedRequest
  low_confidence_terms: string[]
  transfer_id: string | null
  alternatives?: { field: 'sample_id' | 'from' | 'to'; options: string[] }[]
  error?: string | null
}

export interface ConfirmationRequest {
  transfer_id: string
  readback_text: string
  sample_id?: string
  from?: string
  to?: string
  requested_arrival?: string
  estimated_arrival?: string
  audio_url?: string | null
}

export interface CourierPosition {
  transfer_id: string
  x: number
  y: number
  progress: number
}

export type StreamEvent =
  | { kind: 'ledger'; data: LedgerEntry }
  | { kind: 'transfer'; data: Transfer }
  | { kind: 'scan'; data: ScanEvent }
  | { kind: 'approval'; data: Approval }
  | { kind: 'confirmation_request'; data: ConfirmationRequest }
  | { kind: 'courier_position'; data: CourierPosition }

export type ConnectionStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected'

/** Optional authoritative state, fetched on (re)connect when the backend supports GET /state. */
export interface Snapshot {
  transfers?: Transfer[]
  ledger?: LedgerEntry[]
  scans?: ScanEvent[]
  approvals?: Approval[]
  samples?: Sample[]
  locations?: LabLocation[]
  grants?: Grant[]
  demo_scripted?: boolean
  demo_speed?: number
}

// POST /audit/query: expected contract (not yet in ARCHITECTURE.md beyond the route).
export interface VoiceRequestDoc {
  _id: string
  source: 'voice' | 'typed'
  speaker_id: string
  transcript: string
  audio_sha256?: string | null
  audio_verified?: boolean | null
  audio_duration_secs?: number | null
  low_confidence_terms?: string[]
  created_at: string
}

export interface AuditResult {
  answer?: string | null
  mode?: 'canned' | 'semantic'
  entries: LedgerEntry[]
  scans?: ScanEvent[]
  requests?: VoiceRequestDoc[]
  gaps?: LedgerEntry[]
}

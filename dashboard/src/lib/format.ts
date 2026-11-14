import type { Approval, Decision, Identity, LedgerEntry, OutcomeStatus, ScanEvent, TransferState } from './types'

// ---------- status labels ----------

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'mute'

export interface StatusView {
  label: string
  tone: Tone
}

export function decisionView(d: Decision): StatusView {
  switch (d) {
    case 'allow':
      return { label: 'Allowed', tone: 'ok' }
    case 'deny':
      return { label: 'Denied', tone: 'bad' }
    case 'pending_approval':
      return { label: 'Needs approval', tone: 'warn' }
    case 'pending_confirmation':
      return { label: 'Needs confirmation', tone: 'warn' }
    case 'halt':
      return { label: 'Halted', tone: 'bad' }
  }
}

/** SENT is deliberately NOT green: only a verification/scan makes something CONFIRMED. */
export function outcomeView(s: OutcomeStatus): StatusView {
  switch (s) {
    case 'sent':
      return { label: 'Sent', tone: 'info' }
    case 'confirmed':
      return { label: 'Confirmed', tone: 'ok' }
    case 'mismatch':
      return { label: 'Mismatch', tone: 'bad' }
    case 'unconfirmed':
      return { label: 'Unconfirmed', tone: 'warn' }
  }
}

/**
 * Effective verification of an action, by correlation_id:
 * confirmed only if a verification ledger entry says so OR a matching scan arrived.
 */
export function verificationFor(
  entry: LedgerEntry,
  ledger: LedgerEntry[],
  scans: ScanEvent[],
): OutcomeStatus | null {
  if (entry.entry_type !== 'outcome' || entry.status == null) return entry.status ?? null
  if (entry.status !== 'sent') return entry.status
  const verified = ledger.find(
    (e) => e.entry_type === 'verification' && e.correlation_id === entry.correlation_id && e.status,
  )
  if (verified?.status) return verified.status
  if (scans.some((s) => s.correlation_id === entry.correlation_id)) return 'confirmed'
  return 'sent'
}

export function stateLabel(s: TransferState): string {
  const map: Record<TransferState, string> = {
    requested: 'Requested',
    reserved: 'Reserved',
    checked_out: 'Checked out',
    in_transit: 'In transit',
    received: 'Received',
    blocked: 'Blocked',
    revoked: 'Revoked',
    recovering: 'Recovering',
    recovered: 'Recovered',
    cancelled: 'Cancelled',
  }
  return map[s]
}

export function stateTone(s: TransferState): Tone {
  switch (s) {
    case 'received':
    case 'recovered':
    case 'checked_out':
    case 'in_transit':
      return 'ok'
    case 'blocked':
    case 'recovering':
      return 'warn'
    case 'revoked':
    case 'cancelled':
      return 'bad'
    default:
      return 'info'
  }
}

const TOOL_TITLES: Record<string, string> = {
  lookup_sample: 'Look up sample',
  reserve_station: 'Reserve station',
  request_courier: 'Request courier',
  notify: 'Notify destination',
  checkout_sample: 'Check out sample',
  handoff_to_courier: 'Hand off to courier',
  deliver: 'Deliver to destination',
  cancel_reservation: 'Cancel reservation',
  cancel_courier_job: 'Cancel courier job',
  return_to_storage: 'Return to storage',
}

export const toolLabel = (tool: string | null | undefined): string => {
  if (!tool) return ''
  if (TOOL_TITLES[tool]) return TOOL_TITLES[tool]
  const s = tool.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// ---------- exposure countdown ----------

export type ExposureLevel = 'safe' | 'approaching' | 'exceeded'

/** Seconds left (may be negative). Starts from the scanner-verified checkout time only. */
export function remainingSeconds(checkedOutAtIso: string, maxMinutesOut: number, nowMs: number): number {
  const started = Date.parse(checkedOutAtIso)
  if (Number.isNaN(started)) return Number.NaN
  return Math.floor((started + maxMinutesOut * 60_000 - nowMs) / 1000)
}

export function exposureLevel(remainingSecs: number, maxMinutesOut: number): ExposureLevel {
  if (remainingSecs <= 0) return 'exceeded'
  return remainingSecs <= maxMinutesOut * 60 * 0.33 ? 'approaching' : 'safe'
}

export function formatClock(totalSeconds: number): string {
  const sign = totalSeconds < 0 ? '+' : ''
  const s = Math.abs(totalSeconds)
  const m = Math.floor(s / 60)
  return `${sign}${m}:${String(s % 60).padStart(2, '0')}`
}

// ---------- permissions ----------

/** UI hint only. The backend remains the authority on approvals. */
export function canDecideApproval(identity: Identity, approval: Approval): boolean {
  if (approval.status !== 'pending') return false
  const required = approval.approver_role ?? 'supervisor'
  return identity.role === required
}

// ---------- time ----------

export const formatTime = (iso: string): string => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

export const formatDateTime = (iso: string): string => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { hour12: false })
}

export const shortId = (id: string): string => (id.length > 10 ? `${id.slice(0, 4)}…${id.slice(-4)}` : id)

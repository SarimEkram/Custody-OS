// Turns the raw ledger (decision → outcome → verification rows) into one readable item per
// action. Pure and derived: nothing here invents data or upgrades a status on its own.
import type { Tone } from './format'
import type { LedgerEntry, OutcomeStatus, ScanEvent } from './types'

export type StepState = 'done' | 'active' | 'todo' | 'failed' | 'skipped'

export interface ActionStep {
  key: 'policy' | 'executed' | 'scanned'
  label: string
  state: StepState
}

export interface ActionItem {
  id: string
  kind: 'action' | 'event'
  at: string
  /** The entry the evidence drawer opens. */
  entry: LedgerEntry
  title: string
  detail: string
  tone: Tone
  status: string
  steps: ActionStep[]
  physical: boolean
  verified: boolean
  /** Effective verification of this action, for filtering. */
  verification: OutcomeStatus | null
}

const ACTION_TYPES = new Set(['decision', 'outcome', 'verification'])

/** A tool call is physical when it expects a scanner event: irreversible, or a verification row exists. */
function isPhysical(group: LedgerEntry[], scans: ScanEvent[]): boolean {
  const cid = group[0].correlation_id
  return group.some((e) => e.reversible === false) || group.some((e) => e.entry_type === 'verification') || scans.some((s) => s.correlation_id === cid)
}

export function groupLedger(ledger: LedgerEntry[], scans: ScanEvent[]): ActionItem[] {
  const groups = new Map<string, LedgerEntry[]>()
  const order: (string | LedgerEntry)[] = []

  for (const e of ledger) {
    const grouped = ACTION_TYPES.has(e.entry_type) && !!e.tool
    if (!grouped) {
      order.push(e)
      continue
    }
    const g = groups.get(e.correlation_id)
    if (g) g.push(e)
    else {
      groups.set(e.correlation_id, [e])
      order.push(e.correlation_id)
    }
  }

  const items: ActionItem[] = []
  for (const o of order) {
    if (typeof o !== 'string') {
      items.push(eventItem(o))
      continue
    }
    const group = groups.get(o)!
    items.push(actionItem(group, scans))
  }
  return items
}

function actionItem(group: LedgerEntry[], scans: ScanEvent[]): ActionItem {
  const decision = group.find((e) => e.entry_type === 'decision') ?? group[0]
  const outcome = group.find((e) => e.entry_type === 'outcome')
  const verification = group.find((e) => e.entry_type === 'verification')
  const scanned = scans.some((s) => s.correlation_id === decision.correlation_id)
  const physical = isPhysical(group, scans)
  const last = group[group.length - 1]

  const allowed = decision.decision === 'allow'
  const vStatus: OutcomeStatus | null = verification?.status ?? (scanned ? 'confirmed' : null)
  const verified = vStatus === 'confirmed'

  const steps: ActionStep[] = [
    { key: 'policy', label: 'Policy', state: allowed ? 'done' : decision.decision === 'halt' || decision.decision === 'deny' ? 'failed' : 'active' },
    { key: 'executed', label: physical ? 'Sent' : 'Done', state: !allowed ? 'skipped' : outcome ? (outcome.status === 'mismatch' ? 'failed' : 'done') : verification ? 'done' : 'active' },
  ]
  if (physical) {
    steps.push({
      key: 'scanned',
      label: 'Scanned',
      state: !allowed ? 'skipped' : verified ? 'done' : vStatus === 'mismatch' ? 'failed' : outcome ? 'active' : 'todo',
    })
  }

  let tone: Tone = 'ok'
  let status = 'Done'
  if (!allowed) {
    tone = decision.decision === 'deny' || decision.decision === 'halt' ? 'bad' : 'warn'
    status = decision.decision === 'pending_approval' ? 'Needs approval' : decision.decision === 'pending_confirmation' ? 'Needs confirmation' : decision.decision === 'deny' ? 'Denied' : 'Halted'
  } else if (physical) {
    if (verified) {
      tone = 'ok'
      status = 'Verified by scanner'
    } else if (vStatus === 'mismatch') {
      tone = 'bad'
      status = 'Scan mismatch'
    } else if (vStatus === 'unconfirmed') {
      tone = 'warn'
      status = 'Unconfirmed'
    } else if (outcome) {
      tone = 'info'
      status = 'Sent, awaiting scan'
    } else {
      tone = 'info'
      status = 'Running'
    }
  } else if (!outcome) {
    tone = 'info'
    status = 'Running'
  }

  return {
    id: decision.correlation_id,
    kind: 'action',
    at: group[0].timestamp,
    entry: decision,
    title: decision.tool ?? decision.entry_type,
    detail: (last.summary ?? decision.summary ?? '').replace(/\s*\(policy v\d+\)\.?$/, '.'),
    tone,
    status,
    steps,
    physical,
    verified,
    verification: verified ? 'confirmed' : (vStatus ?? (physical && outcome ? 'sent' : null)),
  }
}

const EVENT_TITLES: Record<string, string> = {
  intake: 'Request received',
  confirmation: 'Read-back confirmed',
  compensation: 'Rolled back',
  recovery: 'Recovery started',
  exposure: 'Exposure recorded',
  violation: 'Violation',
  correction: 'Correction',
  decision: 'Policy decision',
  outcome: 'Result',
  verification: 'Verification',
}

function eventItem(e: LedgerEntry): ActionItem {
  const tone: Tone =
    e.decision === 'halt' || e.entry_type === 'violation'
      ? 'bad'
      : e.entry_type === 'recovery'
        ? 'warn'
        : e.entry_type === 'exposure'
          ? e.response?.exceeded
            ? 'bad'
            : 'ok'
          : e.decision === 'allow'
            ? 'ok'
            : 'warn'
  const title = e.decision === 'halt' ? 'Agent halted' : (EVENT_TITLES[e.entry_type] ?? e.entry_type)
  return {
    id: e._id,
    kind: 'event',
    at: e.timestamp,
    entry: e,
    title,
    detail: e.summary ?? '',
    tone,
    status: '',
    steps: [],
    physical: false,
    verified: false,
    verification: null,
  }
}

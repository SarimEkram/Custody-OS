// Fallback for the Auditor when POST /audit/query is unavailable. It only filters
// evidence ALREADY received over the live stream (real ledger rows and scans); it never
// fabricates records. The UI labels results from here as "local live ledger".
import type { Approval, AuditResult, LedgerEntry, Sample, ScanEvent } from './types'

const IRREVERSIBLE = ['checkout_sample', 'handoff_to_courier', 'deliver']

export interface LocalEvidence {
  ledger: LedgerEntry[]
  scans: ScanEvent[]
  approvals: Record<string, Approval>
  samples: Record<string, Sample>
}

/** Irreversible actions that were sent but have no matching scan (DATA_MODEL "Gaps"). */
export function findGaps(ledger: LedgerEntry[], scans: ScanEvent[]): LedgerEntry[] {
  return ledger.filter(
    (e) =>
      e.entry_type === 'outcome' &&
      e.status === 'sent' &&
      e.reversible === false &&
      !scans.some((s) => s.correlation_id === e.correlation_id) &&
      !ledger.some((v) => v.entry_type === 'verification' && v.correlation_id === e.correlation_id && v.status === 'confirmed'),
  )
}

/** Allowed irreversible actions on forensic samples with no approved approval for the transfer. */
export function restrictedWithoutSupervisor(ev: LocalEvidence): LedgerEntry[] {
  return ev.ledger.filter(
    (e) =>
      e.entry_type === 'decision' &&
      e.decision === 'allow' &&
      !!e.tool &&
      IRREVERSIBLE.includes(e.tool) &&
      !!e.sample_id &&
      ev.samples[e.sample_id]?.restriction === 'forensic' &&
      !Object.values(ev.approvals).some((a) => a.transfer_id === e.transfer_id && a.status === 'approved'),
  )
}

export function localAudit(question: string, ev: LocalEvidence): AuditResult {
  const q = question.toLowerCase()
  const sample = /\b([A-Z]{2}-\d{4})\b/i.exec(question)?.[1]?.toUpperCase()
  const grant = /\b(GRANT-\d{4})\b/i.exec(question)?.[1]?.toUpperCase()
  const note = 'Answered from the local live ledger because the audit service is unavailable.'

  if (q.includes('gap')) {
    const gaps = findGaps(ev.ledger, ev.scans)
    return { mode: 'canned', answer: `${note} ${gaps.length} irreversible action(s) were sent without a matching scan.`, entries: gaps, scans: ev.scans }
  }
  if (q.includes('without') && (q.includes('supervisor') || q.includes('approval'))) {
    const rows = restrictedWithoutSupervisor(ev)
    return { mode: 'canned', answer: `${note} ${rows.length} restricted move(s) were allowed without a recorded supervisor approval.`, entries: rows, scans: ev.scans }
  }
  if (grant) {
    const rows = ev.ledger.filter((e) => e.grant_id === grant)
    return { mode: 'canned', answer: `${note} ${rows.length} ledger entries under ${grant}.`, entries: rows, scans: ev.scans }
  }
  if (sample) {
    const rows = ev.ledger.filter((e) => e.sample_id === sample)
    return { mode: 'canned', answer: `${note} ${rows.length} ledger entries for ${sample}.`, entries: rows, scans: ev.scans.filter((s) => s.sample_id === sample) }
  }
  return { mode: 'canned', answer: `${note} Try a sample ID (e.g. TS-1077), a grant ID, or “custody gaps”.`, entries: [], scans: [] }
}

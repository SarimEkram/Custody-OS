import { ArrowRight } from 'lucide-react'
import { decisionView, formatDateTime, outcomeView, toolLabel } from '../../lib/format'
import type { LedgerEntry, ScanEvent } from '../../lib/types'
import { useCustodyStore } from '../../store/custodyStore'
import { Badge, Mono } from '../ui/Badge'
import { Drawer } from '../ui/Drawer'

function Field({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-2 border-b border-line/50 py-1.5 text-sm">
      <dt className="text-muted">{k}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

const json = (v: unknown) => (
  <pre className="max-h-40 overflow-auto rounded-md bg-bg p-2 font-mono text-xs">{JSON.stringify(v, null, 2)}</pre>
)

/** Policy decision → tool execution → scanner verification, joined by correlation_id. */
export function CausalChain({ entry, ledger, scans }: { entry: LedgerEntry; ledger: LedgerEntry[]; scans: ScanEvent[] }) {
  const group = ledger.filter((e) => e.correlation_id === entry.correlation_id)
  const decision = group.find((e) => e.entry_type === 'decision')
  const outcome = group.find((e) => e.entry_type === 'outcome')
  const verification = group.find((e) => e.entry_type === 'verification')
  const scan = scans.find((s) => s.correlation_id === entry.correlation_id)

  const step = (title: string, body: React.ReactNode) => (
    <div className="flex-1 rounded-md border border-line bg-panel p-2 text-xs">
      <div className="mb-1 font-medium text-muted">{title}</div>
      {body}
    </div>
  )

  return (
    <div className="flex items-stretch gap-1.5">
      {step('Policy decision', decision ? <Badge tone={decisionView(decision.decision).tone} label={decisionView(decision.decision).label} /> : <span className="text-muted">not seen</span>)}
      <ArrowRight size={14} className="mt-6 shrink-0 text-muted" aria-hidden />
      {step(
        'Sent to lab system',
        outcome?.status ? <Badge tone={outcomeView(outcome.status).tone} label={outcomeView(outcome.status).label} /> : <span className="text-muted">{decision?.decision === 'allow' ? 'not reported' : 'not run'}</span>,
      )}
      <ArrowRight size={14} className="mt-6 shrink-0 text-muted" aria-hidden />
      {step(
        'Scanner verification',
        scan || verification?.status === 'confirmed' ? (
          <Badge tone="ok" label="CONFIRMED" />
        ) : verification?.status ? (
          <Badge tone={outcomeView(verification.status).tone} label={outcomeView(verification.status).label} />
        ) : (
          <span className="text-muted">{outcome?.status === 'sent' ? 'awaiting scan' : 'n/a'}</span>
        ),
      )}
    </div>
  )
}

export function EvidenceDrawer({
  entry,
  onClose,
  ledger: ledgerOverride,
  scans: scansOverride,
}: {
  entry: LedgerEntry | null
  onClose: () => void
  /** Auditor results pass their own evidence; the live view uses the store. */
  ledger?: LedgerEntry[]
  scans?: ScanEvent[]
}) {
  const liveLedger = useCustodyStore((s) => s.ledger)
  const liveScans = useCustodyStore((s) => s.scans)
  const ledger = ledgerOverride ?? liveLedger
  const scans = scansOverride ?? liveScans
  if (!entry) return null
  const scan = scans.find((s) => s.correlation_id === entry.correlation_id)
  const d = decisionView(entry.decision)

  return (
    <Drawer open title={toolLabel(entry.tool) || entry.entry_type.charAt(0).toUpperCase() + entry.entry_type.slice(1)} onClose={onClose}>
      <CausalChain entry={entry} ledger={ledger} scans={scans} />
      <dl className="mt-4">
        <Field k="Decision"><Badge tone={d.tone} label={d.label} /></Field>
        {entry.status && <Field k="Outcome"><Badge tone={outcomeView(entry.status).tone} label={outcomeView(entry.status).label} /></Field>}
        <Field k="Timestamp">{formatDateTime(entry.timestamp)}</Field>
        <Field k="Actor"><Mono>{entry.actor}</Mono></Field>
        <Field k="On behalf of"><Mono>{entry.on_behalf_of}</Mono></Field>
        <Field k="Grant"><Mono>{entry.grant_id ?? 'none'}</Mono></Field>
        <Field k="Policy"><Mono>{entry.policy_id}@{entry.policy_version}</Mono></Field>
        <Field k="Agent reason">{entry.reason ?? <span className="text-muted">none recorded</span>}</Field>
        <Field k="Rule hits">{entry.rule_hits?.length ? entry.rule_hits.map((r) => <Mono key={r} className="mr-2">{r}</Mono>) : <span className="text-muted">none</span>}</Field>
        {entry.risk_flags && entry.risk_flags.length > 0 && <Field k="Risk flags">{entry.risk_flags.join(', ')}</Field>}
        <Field k="Correlation ID"><Mono>{entry.correlation_id}</Mono></Field>
        <Field k="Request ID"><Mono>{entry.request_id ?? 'none'}</Mono></Field>
        <Field k="Transfer"><Mono>{entry.transfer_id}</Mono> {entry.sample_id && <Mono>· {entry.sample_id}</Mono>}</Field>
        <Field k="Reversible">{entry.reversible == null ? 'n/a' : entry.reversible ? 'Yes (can be rolled back)' : 'No (needs recovery)'}</Field>
        {entry.args && <Field k="Arguments">{json(entry.args)}</Field>}
        {entry.response && <Field k="Outcome detail">{json(entry.response)}</Field>}
        <Field k="Scanner evidence">
          {scan ? (
            <div className="space-y-0.5">
              <div><Badge tone="ok" label="SCAN RECEIVED" /></div>
              <div><Mono>{scan.type}</Mono> at <Mono>{scan.location_id}</Mono></div>
              <div className="text-muted">{formatDateTime(scan.scanned_at)}{scan.scanner_id ? ` · ${scan.scanner_id}` : ''}</div>
              {scan.signature && <div className="truncate font-mono text-xs text-muted" title={scan.signature}>{scan.signature}</div>}
            </div>
          ) : (
            <span className="text-muted">No matching scan for this correlation ID.</span>
          )}
        </Field>
      </dl>
    </Drawer>
  )
}

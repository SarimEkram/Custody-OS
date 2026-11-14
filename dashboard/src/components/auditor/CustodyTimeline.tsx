import { AlertTriangle, CheckCircle2, ClipboardCheck, FileAudio, Gavel, OctagonX, RotateCcw, ScanLine, ShieldCheck, Undo2, type LucideIcon } from 'lucide-react'
import { useMemo } from 'react'
import { decisionView, formatTime, humanizeText, toolLabel } from '../../lib/format'
import { explainRunEnd, isRunEnd, runEndStatus } from '../../lib/runEnd'
import type { LedgerEntry, ScanEvent, VoiceRequestDoc } from '../../lib/types'
import { Badge, Mono, toneText } from '../ui/Badge'
import type { Tone } from '../../lib/format'

export type TimelineItem = {
  id: string
  at: string
  icon: LucideIcon
  tone: Tone
  stage: string
  title: string
  detail?: string
  entry?: LedgerEntry
  missing?: boolean
}

export function buildTimeline(entries: LedgerEntry[], scans: ScanEvent[], requests: VoiceRequestDoc[]): TimelineItem[] {
  const items: TimelineItem[] = []

  for (const r of requests) {
    items.push({
      id: `req-${r._id}`,
      at: r.created_at,
      icon: FileAudio,
      tone: 'info',
      stage: 'Original request',
      title: `“${r.transcript}”`,
      detail: `${r.source === 'voice' ? 'Spoken' : 'Typed'} by ${r.speaker_id} (identity from login, not voice)`,
    })
  }

  for (const e of entries) {
    const d = decisionView(e.decision)
    const base = { id: e._id, at: e.timestamp, entry: e, detail: humanizeText(e.summary) }
    switch (e.entry_type) {
      case 'intake':
        // Covered by the original-request row when the request doc is present.
        if (requests.length === 0) items.push({ ...base, icon: FileAudio, tone: 'info', stage: 'Original request', title: `Intake by ${e.actor}` })
        break
      case 'confirmation':
        items.push({ ...base, icon: ClipboardCheck, tone: 'ok', stage: 'Read-back confirmation', title: `Confirmed by ${String(e.response?.confirmed_by ?? e.on_behalf_of)}` })
        break
      case 'decision':
        items.push({ ...base, icon: e.decision === 'halt' ? OctagonX : ShieldCheck, tone: d.tone, stage: e.decision === 'halt' ? 'Revocation / halt' : 'Policy decision', title: `${toolLabel(e.tool)} · ${d.label}`, detail: `${humanizeText(e.summary)} Policy ${e.policy_id}@${e.policy_version}`.trim() })
        break
      case 'outcome': {
        const scanned = scans.some((s) => s.correlation_id === e.correlation_id)
        const verified = entries.some((v) => v.entry_type === 'verification' && v.correlation_id === e.correlation_id)
        const gap = e.status === 'sent' && e.reversible === false && !scanned && !verified
        items.push({ ...base, icon: gap ? AlertTriangle : CheckCircle2, tone: gap ? 'bad' : 'info', stage: 'Lab service action', title: `${toolLabel(e.tool)} · ${e.status === 'confirmed' ? 'Service confirmed' : e.status === 'sent' ? 'Sent' : (e.status ?? 'Sent')}`, missing: gap })
        break
      }
      case 'verification':
        items.push({ ...base, icon: ScanLine, tone: e.status === 'confirmed' ? 'ok' : 'bad', stage: 'Scanner confirmation', title: `${toolLabel(e.tool) || 'Verification'} · ${e.status === 'confirmed' ? 'Verified by scanner' : (e.status ?? '')}` })
        break
      case 'compensation':
        items.push({ ...base, icon: Undo2, tone: 'ok', stage: 'Compensation (rollback)', title: toolLabel(e.tool) || 'Compensation' })
        break
      case 'recovery':
        items.push({ ...base, icon: RotateCcw, tone: 'warn', stage: 'Recovery', title: toolLabel(e.tool) || 'Recovery' })
        break
      case 'exposure':
        items.push({ ...base, icon: Gavel, tone: e.response?.exceeded ? 'bad' : 'ok', stage: 'Final exposure', title: `${String(e.response?.minutes_out ?? '?')} min out · ${e.response?.exceeded ? 'limit exceeded' : 'within limit'}` })
        break
      case 'violation':
        if (isRunEnd(e)) {
          items.push({ ...base, icon: AlertTriangle, tone: 'warn', stage: 'Run ended early (a failure, not a policy decision)', title: explainRunEnd(runEndStatus(e)).title })
          break
        }
        items.push({ ...base, icon: AlertTriangle, tone: 'bad', stage: 'Violation', title: humanizeText(e.summary) || 'Violation recorded' })
        break
      default:
        items.push({ ...base, icon: ShieldCheck, tone: 'mute', stage: e.entry_type, title: humanizeText(e.summary) || e.entry_type })
    }
  }

  for (const s of scans) {
    items.push({
      id: `scan-${s._id ?? `${s.correlation_id}-${s.type}-${s.scanned_at}`}`,
      at: s.scanned_at,
      icon: ScanLine,
      tone: 'ok',
      stage: s.type === 'handoff' ? 'Custody handoff (scanned)' : 'Scanner confirmation',
      title: `${s.type} at ${s.location_id}`,
      detail: [s.from_custodian, s.to_custodian].filter(Boolean).join(' → ') || undefined,
    })
  }

  return items.sort((a, b) => a.at.localeCompare(b.at))
}

export function CustodyTimeline({ entries, scans, requests, onOpen }: { entries: LedgerEntry[]; scans: ScanEvent[]; requests: VoiceRequestDoc[]; onOpen: (e: LedgerEntry) => void }) {
  const items = useMemo(() => buildTimeline(entries, scans, requests), [entries, scans, requests])
  const gaps = items.filter((i) => i.missing).length
  if (items.length === 0) return <p className="p-4 text-sm text-muted">No evidence to show yet.</p>
  return (
    <div>
      {gaps > 0 && (
        <p role="alert" className="mb-3 flex items-center gap-2 rounded-md border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">
          <AlertTriangle size={16} aria-hidden /> {gaps} action(s) were sent but have no scanner verification.
        </p>
      )}
      <ol className="relative space-y-3 border-l border-line pl-5">
        {items.map((i) => {
          const Icon = i.icon
          const content = (
            <>
              <span className={`absolute -left-[31px] flex size-6 items-center justify-center rounded-full border border-line bg-surface ${toneText[i.tone]}`}>
                <Icon size={14} aria-hidden />
              </span>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <span className="font-medium text-fg/80">{i.stage}</span>
                <Mono>{formatTime(i.at)}</Mono>
                {i.missing && <Badge tone="bad" icon={AlertTriangle} label="Missing verification" />}
              </div>
              <div className="text-sm font-medium">{i.title}</div>
              {i.detail && <div className="text-[13px] text-muted">{i.detail}</div>}
            </>
          )
          return (
            <li key={i.id} className="relative">
              {i.entry ? (
                <button onClick={() => onOpen(i.entry!)} className="block w-full rounded-md px-2 py-1 text-left hover:bg-panel">{content}</button>
              ) : (
                <div className="px-2 py-1">{content}</div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

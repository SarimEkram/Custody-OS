import { Check, ChevronRight, ListFilter, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { groupLedger, type ActionItem, type StepState } from '../../lib/actions'
import { decisionView, formatTime, outcomeView, toolLabel } from '../../lib/format'
import type { Decision, OutcomeStatus } from '../../lib/types'
import { useCustodyStore } from '../../store/custodyStore'
import { toneText } from '../ui/Badge'
import { Panel } from '../ui/Panel'
import { EvidenceDrawer } from './EvidenceDrawer'

const STEP_TITLE: Record<string, string> = {
  policy: 'Policy decision',
  executed: 'Sent to the lab system',
  scanned: 'Verified by the scanner',
}

/** Policy → Sent → Scanned. "Sent" is never shown as verified: only the third step is proof. */
function Tracker({ item }: { item: ActionItem }) {
  if (item.steps.length === 0) return null
  const color = (s: StepState) => (s === 'done' ? 'bg-ok text-bg' : s === 'failed' ? 'bg-bad text-bg' : s === 'active' ? 'ring-2 ring-info text-info' : s === 'skipped' ? 'bg-line/50' : 'ring-1 ring-line')
  return (
    <ol className="flex shrink-0 items-center" aria-label={`Progress: ${item.steps.map((s) => `${s.label} ${s.state}`).join(', ')}`}>
      {item.steps.map((s, i) => (
        <li key={s.key} className="flex items-center" title={`${STEP_TITLE[s.key]}: ${s.state === 'done' ? 'yes' : s.state === 'active' ? 'in progress' : s.state === 'failed' ? 'failed' : s.state === 'skipped' ? 'not run' : 'not yet'}`}>
          {i > 0 && <span className={`h-px w-3 ${item.steps[i - 1].state === 'done' ? 'bg-ok/60' : 'bg-line'}`} />}
          <span key={s.state} className={`flex size-[18px] items-center justify-center rounded-full ${color(s.state)} ${s.state === 'done' ? 'tick-pop' : ''}`}>
            {s.state === 'done' && <Check size={11} strokeWidth={3} aria-hidden />}
            {s.state === 'failed' && <X size={11} strokeWidth={3} aria-hidden />}
          </span>
        </li>
      ))}
    </ol>
  )
}

function Row({ item, onOpen }: { item: ActionItem; onOpen: () => void }) {
  const isAction = item.kind === 'action'
  return (
    <li className="row-in">
      <button onClick={onOpen} className="group grid w-full grid-cols-[52px_1fr_auto] items-start gap-x-3 rounded-lg px-3 py-2.5 text-left hover:bg-panel/70">
        <span className="pt-[3px] font-mono text-[11px] text-muted/80">{formatTime(item.at)}</span>
        <span className="min-w-0">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[14px] font-medium">{isAction ? toolLabel(item.entry.tool) : item.title}</span>
            {isAction && <span className={`text-xs font-medium ${toneText[item.tone]}`}>{item.status}</span>}
          </span>
          {item.detail && <span className="mt-0.5 line-clamp-2 block text-[13px] leading-snug text-muted">{item.detail}</span>}
        </span>
        <span className="flex items-center gap-2 pt-0.5">
          {isAction ? <Tracker item={item} /> : <span className={`mt-1.5 size-2 rounded-full ${item.tone === 'bad' ? 'bg-bad' : item.tone === 'warn' ? 'bg-warn' : 'bg-ok'}`} aria-hidden />}
          <ChevronRight size={15} className="text-line group-hover:text-muted" aria-hidden />
        </span>
      </button>
    </li>
  )
}

interface Filters {
  sample: string
  transfer: string
  actor: string
  decision: '' | Decision
  verification: '' | OutcomeStatus
}
const NO_FILTER: Filters = { sample: '', transfer: '', actor: '', decision: '', verification: '' }

export function LedgerStream() {
  const ledger = useCustodyStore((s) => s.ledger)
  const scans = useCustodyStore((s) => s.scans)
  const connection = useCustodyStore((s) => s.connection)
  const selectedId = useCustodyStore((s) => s.selectedEntryId)
  const selectEntry = useCustodyStore((s) => s.selectEntry)
  const [filters, setFilters] = useState<Filters>(NO_FILTER)
  const [showFilters, setShowFilters] = useState(false)
  const [live, setLive] = useState(false)
  const listRef = useRef<HTMLUListElement>(null)
  const stick = useRef(true)

  useEffect(() => {
    const t = setTimeout(() => setLive(true), 700)
    return () => clearTimeout(t)
  }, [])

  const items = useMemo(() => groupLedger(ledger, scans), [ledger, scans])
  const options = useMemo(() => {
    const uniq = (f: (e: (typeof ledger)[number]) => string | null | undefined) => [...new Set(ledger.map(f).filter((x): x is string => !!x))]
    return { samples: uniq((e) => e.sample_id), transfers: uniq((e) => e.transfer_id), actors: uniq((e) => e.actor) }
  }, [ledger])

  const rows = items.filter(
    (i) =>
      (!filters.sample || i.entry.sample_id === filters.sample) &&
      (!filters.transfer || i.entry.transfer_id === filters.transfer) &&
      (!filters.actor || i.entry.actor === filters.actor) &&
      (!filters.decision || i.entry.decision === filters.decision) &&
      (!filters.verification || i.verification === filters.verification),
  )

  useEffect(() => {
    const el = listRef.current
    if (el && stick.current) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [rows.length])

  const selected = ledger.find((e) => e._id === selectedId) ?? null
  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => setFilters((f) => ({ ...f, [k]: v }))
  const active = Object.values(filters).filter(Boolean).length
  const sel = 'rounded-md bg-panel px-2 py-1.5 text-xs text-fg ring-1 ring-line/70'

  return (
    <Panel
      title="Activity"
      className="h-full"
      bodyClass="flex min-h-0 flex-col p-0"
      actions={
        <button
          onClick={() => setShowFilters((v) => !v)}
          aria-expanded={showFilters}
          className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-xs ${active ? 'text-info' : 'text-muted hover:text-fg'}`}
        >
          <ListFilter size={13} aria-hidden /> Filter{active > 0 && ` (${active})`}
        </button>
      }
    >
      {showFilters && (
        <div className="flex flex-wrap gap-1.5 px-4 pb-2" aria-label="Ledger filters">
          <select className={sel} value={filters.sample} onChange={(e) => set('sample', e.target.value)} aria-label="Sample">
            <option value="">All samples</option>
            {options.samples.map((o) => <option key={o}>{o}</option>)}
          </select>
          <select className={sel} value={filters.transfer} onChange={(e) => set('transfer', e.target.value)} aria-label="Transfer">
            <option value="">All transfers</option>
            {options.transfers.map((o) => <option key={o}>{o}</option>)}
          </select>
          <select className={sel} value={filters.actor} onChange={(e) => set('actor', e.target.value)} aria-label="Actor">
            <option value="">All actors</option>
            {options.actors.map((o) => <option key={o}>{o}</option>)}
          </select>
          <select className={sel} value={filters.decision} onChange={(e) => set('decision', e.target.value as Filters['decision'])} aria-label="Decision">
            <option value="">Any decision</option>
            {(['allow', 'deny', 'pending_approval', 'pending_confirmation', 'halt'] as Decision[]).map((o) => <option key={o} value={o}>{decisionView(o).label}</option>)}
          </select>
          <select className={sel} value={filters.verification} onChange={(e) => set('verification', e.target.value as Filters['verification'])} aria-label="Verification">
            <option value="">Any verification</option>
            {(['sent', 'confirmed', 'mismatch', 'unconfirmed'] as OutcomeStatus[]).map((o) => <option key={o} value={o}>{outcomeView(o).label}</option>)}
          </select>
          {active > 0 && <button onClick={() => setFilters(NO_FILTER)} className="px-1 text-xs text-info hover:underline">Clear</button>}
        </div>
      )}

      <ul
        ref={listRef}
        className={`gate min-h-0 flex-1 overflow-y-auto px-1.5 pb-2 ${live ? 'live' : ''}`}
        onScroll={(e) => {
          const el = e.currentTarget
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
        }}
      >
        {rows.map((i) => <Row key={i.id} item={i} onOpen={() => selectEntry(i.entry._id)} />)}
        {rows.length === 0 && (
          <li className="px-4 py-10 text-center text-sm text-muted">
            {ledger.length > 0
              ? 'No activity matches these filters.'
              : connection === 'connected'
                ? 'Nothing yet. Every agent action will appear here, with proof from the scanner.'
                : 'Not connected to the agent service, so there is no activity to show.'}
          </li>
        )}
      </ul>
      <EvidenceDrawer entry={selected} onClose={() => selectEntry(null)} />
    </Panel>
  )
}

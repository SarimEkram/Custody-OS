import { Loader2, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { ApiError, auditQuery } from '../../lib/api'
import { localAudit } from '../../lib/auditLocal'
import { groupLedger } from '../../lib/actions'
import { formatTime, toolLabel } from '../../lib/format'
import type { AuditResult, LedgerEntry } from '../../lib/types'
import { useCustodyStore } from '../../store/custodyStore'
import { EvidenceDrawer } from '../ledger/EvidenceDrawer'
import { Badge, Mono, toneText } from '../ui/Badge'
import { Panel } from '../ui/Panel'
import { AudioEvidencePlayer } from './AudioEvidencePlayer'
import { CustodyTimeline } from './CustodyTimeline'

const CHIPS = [
  'What happened to TS-1077?',
  'Show restricted transfers without supervisor approval.',
  'Find custody gaps.',
  'Show every action performed under GRANT-0007.',
]

export function AuditorPanel() {
  const identityId = useCustodyStore((s) => s.identityId)
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AuditResult | null>(null)
  const [source, setSource] = useState<'service' | 'local' | null>(null)
  const [sampleFilter, setSampleFilter] = useState('')
  const [open, setOpen] = useState<LedgerEntry | null>(null)

  const run = async (q: string) => {
    const text = q.trim()
    if (!text) return
    setQuestion(text)
    setLoading(true)
    setError(null)
    setSampleFilter('')
    try {
      setResult(await auditQuery(text, identityId))
      setSource('service')
    } catch (e) {
      if (e instanceof ApiError && (e.kind === 'not_implemented' || e.kind === 'network')) {
        const st = useCustodyStore.getState()
        setResult(localAudit(text, { ledger: st.ledger, scans: st.scans, approvals: st.approvals, samples: st.samples }))
        setSource('local')
      } else {
        setResult(null)
        setError(e instanceof ApiError ? e.message : 'Audit query failed.')
      }
    } finally {
      setLoading(false)
    }
  }

  const entries = useMemo(() => result?.entries ?? [], [result])
  const samples = useMemo(() => [...new Set(entries.map((e) => e.sample_id).filter((x): x is string => !!x))], [entries])
  const shown = useMemo(() => (sampleFilter ? entries.filter((e) => e.sample_id === sampleFilter) : entries), [entries, sampleFilter])
  const items = useMemo(() => groupLedger(shown, result?.scans ?? []), [shown, result])
  const shownScans = (result?.scans ?? []).filter((s) => !sampleFilter || s.sample_id === sampleFilter)
  const requests = (result?.requests ?? []).filter((r) => shown.some((e) => e.request_id === r._id) || !sampleFilter)
  const timelineScans = sampleFilter || samples.length <= 1 ? shownScans : []

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <Panel title="Ask the ledger" icon={<Search size={16} className="text-info" aria-hidden />}>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void run(question)
          }}
        >
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="What happened to TS-1077?"
            aria-label="Audit question"
            className="min-w-0 flex-1 rounded-md border border-line bg-panel px-3 py-2.5 text-sm"
          />
          <button type="submit" disabled={loading || !question.trim()} className="flex items-center gap-1.5 rounded-md bg-info px-4 py-2.5 text-sm font-semibold text-bg hover:brightness-110">
            {loading ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} aria-hidden />} Search
          </button>
        </form>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {CHIPS.map((c) => (
            <button key={c} onClick={() => void run(c)} className="rounded-full border border-line bg-panel px-3 py-1 text-xs hover:border-info">
              {c}
            </button>
          ))}
        </div>
      </Panel>

      {error && <p role="alert" className="rounded-md border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}

      {result && (
        <>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {source === 'local' ? <Badge tone="warn" label="LOCAL LIVE LEDGER (AUDIT SERVICE UNAVAILABLE)" /> : <Badge tone="ok" label={result.mode === 'semantic' ? 'SEMANTIC SEARCH' : 'AUDIT SERVICE'} />}
            {result.answer && <span className="text-muted">{result.answer}</span>}
          </div>
          <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(240px,1fr)_2fr]">
            <Panel title={`Results (${items.length})`} bodyClass="p-0 overflow-y-auto" className="min-h-64">
              {samples.length > 1 && (
                <div className="flex flex-wrap gap-1.5 border-b border-line p-2">
                  <button onClick={() => setSampleFilter('')} className={`rounded px-2 py-0.5 text-xs ${!sampleFilter ? 'bg-info text-bg' : 'bg-panel'}`}>All</button>
                  {samples.map((s) => (
                    <button key={s} onClick={() => setSampleFilter(s)} className={`rounded px-2 py-0.5 font-mono text-xs ${sampleFilter === s ? 'bg-info text-bg' : 'bg-panel'}`}>{s}</button>
                  ))}
                </div>
              )}
              {entries.length === 0 ? (
                <p className="p-4 text-sm text-ok">Zero rows. Nothing matched, which is the expected result for a clean ledger.</p>
              ) : (
                <ul className="p-1.5">
                  {items.map((i) => (
                    <li key={i.id}>
                      <button onClick={() => setOpen(i.entry)} className="flex w-full flex-col gap-0.5 rounded-lg px-3 py-2 text-left hover:bg-panel">
                        <span className="flex flex-wrap items-baseline gap-x-2">
                          <span className="text-sm font-medium">{i.kind === 'action' ? toolLabel(i.entry.tool) : i.title}</span>
                          {i.kind === 'action' && <span className={`text-xs font-medium ${toneText[i.tone]}`}>{i.status}</span>}
                        </span>
                        <span className="text-xs text-muted">{formatTime(i.at)} · <Mono>{i.entry.sample_id ?? i.entry.transfer_id}</Mono></span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <Panel title="Chain of custody" bodyClass="overflow-y-auto p-4" className="min-h-64">
              {requests.map((r) => (
                <div key={r._id} className="mb-4 rounded-md border border-line bg-panel p-3">
                  <p className="mb-2 text-xs font-medium text-muted">Original request <Mono>{r._id}</Mono></p>
                  <p className="mb-2 text-sm">“{r.transcript}”</p>
                  <AudioEvidencePlayer request={r} />
                </div>
              ))}
              <CustodyTimeline entries={shown} scans={timelineScans} requests={[]} onOpen={setOpen} />
            </Panel>
          </div>
        </>
      )}

      {!result && !error && !loading && <p className="text-sm text-muted">Ask a question or pick a demo query. Every answer links back to ledger entries and scanner evidence.</p>}

      <EvidenceDrawer entry={open} onClose={() => setOpen(null)} ledger={entries} scans={result?.scans ?? []} />
    </div>
  )
}

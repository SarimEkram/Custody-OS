// Some audit queries return summary rows (a few fields, or per-sample aggregates) instead of full ledger
// entries. They can't be shown as ledger rows, but they are exactly what an auditor asked for, so they get
// their own display. Nothing here is invented: every fact shown is a field the backend sent.
import { formatDateTime, toolLabel, type Tone } from './format'
import type { AuditResult, AuditRow } from './types'

export interface Finding {
  id: string
  kind: 'gap' | 'exposure'
  title: string
  detail: string
  tone: Tone
  sampleId: string | null
  facts: [label: string, value: string][]
}

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const isRows = (v: unknown): v is AuditRow[] => Array.isArray(v) && v.every((x) => typeof x === 'object' && x !== null)

function gapFinding(r: AuditRow, i: number): Finding {
  const sample = str(r.sample_id)
  const when = str(r.timestamp)
  const facts: Finding['facts'] = []
  if (sample) facts.push(['Sample', sample])
  if (str(r.transfer_id)) facts.push(['Transfer', str(r.transfer_id)!])
  if (str(r.on_behalf_of)) facts.push(['Requested by', str(r.on_behalf_of)!])
  if (when) facts.push(['Sent at', formatDateTime(when)])
  if (str(r.correlation_id)) facts.push(['Correlation', str(r.correlation_id)!])
  return {
    id: str(r._id) ?? `gap-${i}`,
    kind: 'gap',
    title: toolLabel(str(r.tool)) || 'Action',
    detail: `Sent to a lab system but never confirmed by a scanner.${str(r.summary) ? ' ' + str(r.summary) : ''}`,
    tone: 'bad',
    sampleId: sample,
    facts,
  }
}

function exposureFinding(r: AuditRow, i: number): Finding {
  const sample = str(r._id)
  const minutes = num(r.total_minutes_out)
  const excursions = num(r.excursions) ?? 0
  const transfers = Array.isArray(r.transfers) ? r.transfers.filter((t): t is string => typeof t === 'string') : []
  return {
    id: sample ?? `exp-${i}`,
    kind: 'exposure',
    title: sample ?? 'Sample',
    detail: `${minutes != null ? `${Math.round(minutes * 100) / 100} min` : 'Unknown time'} out of storage${transfers.length ? ` across ${transfers.length} transfer${transfers.length === 1 ? '' : 's'}` : ''}.`,
    tone: excursions > 0 ? 'bad' : 'ok',
    sampleId: sample,
    facts: [
      ['Minutes out', minutes != null ? String(Math.round(minutes * 100) / 100) : '?'],
      ['Excursions over the limit', String(excursions)],
      ...(transfers.length ? ([['Transfers', transfers.join(', ')]] as Finding['facts']) : []),
    ],
  }
}

export function findingsFrom(result: AuditResult | null): Finding[] {
  if (!result) return []
  if (result.route === 'exposure_report' && isRows(result.rows)) return result.rows.map(exposureFinding)
  const gaps = isRows(result.gaps) ? result.gaps : []
  if (gaps.length > 0 && result.entries.length === 0) return gaps.map(gapFinding)
  return []
}

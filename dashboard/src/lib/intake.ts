// Turns the backend's intake `problems` into what the "needs clarification" prompt shows.
import type { IntakeResponse } from './types'

export type AlternativeField = 'sample_id' | 'from' | 'to'
const FIELDS: readonly string[] = ['sample_id', 'from', 'to']

export interface Alternative {
  field: AlternativeField
  options: string[]
}

/** "Did you mean ...?" options. Uses the backend's suggestions, never anything the dashboard guessed. */
export function alternativesFrom(r: IntakeResponse): Alternative[] {
  if (r.alternatives?.length) return r.alternatives
  const out: Alternative[] = []
  for (const p of r.problems ?? []) {
    const options = Array.isArray(p.suggestions) ? p.suggestions : [] // some problems carry none
    if (!FIELDS.includes(p.field) || options.length === 0) continue
    out.push({ field: p.field as AlternativeField, options })
  }
  return out
}

/** Fields the backend could not validate, so the summary can flag them instead of showing them as understood. */
export function problemFields(r: IntakeResponse): Set<string> {
  return new Set((r.problems ?? []).map((p) => p.field))
}

const FALLBACK: Record<string, string> = {
  not_understood: "I didn't understand that request. Try: \u201cMove TS-1042 from Freezer B to Sequencing Station 3 for 2 PM.\u201d",
  missing: 'Part of the request is missing.',
}

/** The backend's own wording ("I can't find TS-1024. Did you mean TS-1042 or TS-1077?"), or a plain fallback. */
export function problemMessages(r: IntakeResponse): string[] {
  return (r.problems ?? []).map((p) => p.message || FALLBACK[p.reason] || '').filter(Boolean)
}

export const needsClarification = (r: IntakeResponse): boolean =>
  (r.problems?.length ?? 0) > 0 ||
  r.low_confidence_terms.length > 0 ||
  !r.transfer_id ||
  !r.parsed.sample_id ||
  !r.parsed.from ||
  !r.parsed.to ||
  (r.alternatives?.length ?? 0) > 0

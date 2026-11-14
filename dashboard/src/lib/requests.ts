// A transfer trace (GET /audit/transfer/{id}) joins each ledger row to the original voice/typed request.
// This pulls those request documents out, so the Auditor can show the transcript and offer the audio.
import type { VoiceRequestDoc } from './types'

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

export function requestsFromTrace(rows: unknown): VoiceRequestDoc[] {
  if (!Array.isArray(rows)) return []
  const seen = new Set<string>()
  const out: VoiceRequestDoc[] = []
  for (const row of rows) {
    if (!isObj(row) || !isObj(row.request)) continue
    const r = row.request
    if (typeof r._id !== 'string' || typeof r.transcript !== 'string' || seen.has(r._id)) continue
    seen.add(r._id)
    out.push(r as unknown as VoiceRequestDoc)
  }
  return out
}

/** Transfers whose original request we don't have yet (the chain query doesn't join requests). */
export function transfersMissingRequests(
  entries: { transfer_id: string; request_id?: string | null }[],
  have: { _id: string }[],
  limit = 4,
): string[] {
  const known = new Set(have.map((r) => r._id))
  const out: string[] = []
  for (const e of entries) {
    if (!e.request_id || known.has(e.request_id) || out.includes(e.transfer_id)) continue
    out.push(e.transfer_id)
    if (out.length >= limit) break
  }
  return out
}

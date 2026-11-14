// Validates raw SSE payloads into typed events. Anything that does not have the
// required documented fields is dropped (returned as null); we never invent fields.
import type {
  Approval,
  ConfirmationRequest,
  CourierSignal,
  LedgerEntry,
  ScanEvent,
  StreamEvent,
  Transfer,
} from './types'

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown): v is string => typeof v === 'string' && v.length > 0

/** MongoDB extended JSON ({"$oid": "..."}, {"$date": "..."}) → plain string. */
function unwrap(v: unknown): unknown {
  if (isObj(v)) {
    if ('$oid' in v) return v.$oid
    if ('$date' in v) return v.$date
  }
  return v
}

function clean(o: Obj): Obj {
  const out: Obj = {}
  for (const [k, v] of Object.entries(o)) out[k] = unwrap(v)
  return out
}

export function normalizeLedger(raw: unknown): LedgerEntry | null {
  if (!isObj(raw)) return null
  const o = clean(raw)
  if (!str(o._id) || !str(o.timestamp) || !str(o.entry_type) || !str(o.transfer_id) || !str(o.decision)) return null
  if (!str(o.actor) || !str(o.correlation_id)) return null
  // `embedding` must never be kept in memory or shown.
  delete o.embedding
  return o as unknown as LedgerEntry
}

export function normalizeTransfer(raw: unknown): Transfer | null {
  if (!isObj(raw)) return null
  const o = clean(raw)
  if (!str(o._id) || !str(o.sample_id) || !str(o.state) || !str(o.from) || !str(o.to)) return null
  return o as unknown as Transfer
}

export function normalizeScan(raw: unknown): ScanEvent | null {
  if (!isObj(raw)) return null
  const o = clean(raw)
  if (!str(o.type) || !str(o.sample_id) || !str(o.location_id) || !str(o.scanned_at)) return null
  return o as unknown as ScanEvent
}

export function normalizeApproval(raw: unknown): Approval | null {
  if (!isObj(raw)) return null
  const o = clean(raw)
  if (!str(o._id) || !str(o.transfer_id) || !str(o.status)) return null
  return o as unknown as Approval
}

export function normalizeConfirmation(raw: unknown): ConfirmationRequest | null {
  if (!isObj(raw)) return null
  const o = clean(raw)
  // The backend's ConfirmationService sends the sentence as `text`; older shapes used `readback_text`.
  const sentence = str(o.readback_text) ? o.readback_text : str(o.text) ? o.text : null
  if (!str(o.transfer_id) || !sentence) return null
  return { ...o, readback_text: sentence } as unknown as ConfirmationRequest
}

export function normalizeCourier(raw: unknown): CourierSignal | null {
  if (!isObj(raw)) return null
  const o = raw
  if (!str(o.transfer_id)) return null
  const hasXY = typeof o.x === 'number' && typeof o.y === 'number'
  const hasLeg = str(o.from) && str(o.to)
  if (!hasXY && !hasLeg) return null // nothing to place the courier with
  return {
    transfer_id: o.transfer_id,
    progress: typeof o.progress === 'number' ? o.progress : 0,
    ...(hasXY ? { x: o.x as number, y: o.y as number } : {}),
    ...(hasLeg ? { from: o.from as string, to: o.to as string } : {}),
    ...(str(o.job_id) ? { job_id: o.job_id } : {}),
    ...(str(o.sample_id) ? { sample_id: o.sample_id } : {}),
    ...(str(o.original_to) ? { original_to: o.original_to } : {}),
    ...(typeof o.return_from_progress === 'number' ? { return_from_progress: o.return_from_progress } : {}),
  }
}

/** Parses one SSE message body. Returns null for malformed or unknown events. */
export function parseStreamEvent(text: string): StreamEvent | null {
  let msg: unknown
  try {
    msg = JSON.parse(text)
  } catch {
    return null
  }
  if (!isObj(msg) || !str(msg.kind)) return null
  switch (msg.kind) {
    case 'ledger': {
      const data = normalizeLedger(msg.data)
      return data && { kind: 'ledger', data }
    }
    case 'transfer': {
      const data = normalizeTransfer(msg.data)
      return data && { kind: 'transfer', data }
    }
    case 'scan': {
      const data = normalizeScan(msg.data)
      return data && { kind: 'scan', data }
    }
    case 'approval': {
      const data = normalizeApproval(msg.data)
      return data && { kind: 'approval', data }
    }
    case 'confirmation_request': {
      const data = normalizeConfirmation(msg.data)
      return data && { kind: 'confirmation_request', data }
    }
    case 'courier_position': {
      const data = normalizeCourier(msg.data)
      return data && { kind: 'courier_position', data }
    }
    default:
      return null
  }
}

/** Stable identity of a scan, for deduplication across reconnects. */
export const scanKey = (s: ScanEvent): string =>
  s._id ?? `${s.correlation_id ?? ''}:${s.type}:${s.sample_id}:${s.scanned_at}`

/** Appends `incoming` to `list`, skipping ids already seen. Keeps chronological order. */
export function mergeById<T>(list: T[], incoming: T[], key: (t: T) => string, time: (t: T) => string): T[] {
  const seen = new Set(list.map(key))
  const fresh = incoming.filter((t) => {
    const k = key(t)
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
  if (fresh.length === 0) return list
  return [...list, ...fresh].sort((a, b) => time(a).localeCompare(time(b)))
}

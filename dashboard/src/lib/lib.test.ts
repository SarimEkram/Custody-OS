import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, sendTypedRequest, sendVoiceRequest } from './api'
import { canDecideApproval, decisionView, elapsedSeconds, exposureLevel, exposureStatus, formatClock, outcomeView, remainingSeconds, stateLabel, verificationFor } from './format'
import { mergeById, parseStreamEvent, scanKey } from './normalize'
import { IDENTITIES } from './seed'
import type { Approval, LedgerEntry, ScanEvent } from './types'

const entry = (over: Partial<LedgerEntry> = {}): LedgerEntry => ({
  _id: 'e1',
  timestamp: '2026-11-14T18:52:11Z',
  entry_type: 'outcome',
  transfer_id: 'TR-0031',
  sample_id: 'TS-1042',
  correlation_id: 'c-7f3a',
  actor: 'agent:custody-01',
  on_behalf_of: 'user:jlee',
  policy_id: 'custody-default',
  policy_version: 3,
  decision: 'allow',
  status: 'sent',
  ...over,
})

const scan = (over: Partial<ScanEvent> = {}): ScanEvent => ({
  type: 'checkout',
  sample_id: 'TS-1042',
  location_id: 'FZ-B',
  correlation_id: 'c-7f3a',
  scanned_at: '2026-11-14T18:52:13Z',
  ...over,
})

describe('transfer state rendering', () => {
  it('labels every documented state', () => {
    expect(stateLabel('in_transit')).toBe('In transit')
    expect(stateLabel('recovering')).toBe('Recovering')
    expect(decisionView('pending_approval')).toEqual({ label: 'Needs approval', tone: 'warn' })
    expect(decisionView('halt').tone).toBe('bad')
  })
})

describe('ledger event normalization', () => {
  const wire = (data: unknown) => JSON.stringify({ kind: 'ledger', data })

  it('accepts a valid entry and unwraps Mongo extended JSON', () => {
    const ev = parseStreamEvent(wire({ ...entry(), _id: { $oid: 'abc' }, timestamp: { $date: '2026-11-14T18:52:11Z' } }))
    expect(ev?.kind).toBe('ledger')
    if (ev?.kind === 'ledger') {
      expect(ev.data._id).toBe('abc')
      expect(ev.data.timestamp).toBe('2026-11-14T18:52:11Z')
    }
  })

  it('strips embeddings so they are never held in the browser', () => {
    const ev = parseStreamEvent(wire({ ...entry(), embedding: [0.1, 0.2] }))
    expect(ev && 'embedding' in (ev.data as object)).toBe(false)
  })

  it('drops malformed payloads and unknown kinds instead of inventing fields', () => {
    expect(parseStreamEvent('not json')).toBeNull()
    expect(parseStreamEvent(wire({ _id: 'x' }))).toBeNull()
    expect(parseStreamEvent(JSON.stringify({ kind: 'mystery', data: {} }))).toBeNull()
  })

  it('parses courier positions', () => {
    const ev = parseStreamEvent(JSON.stringify({ kind: 'courier_position', data: { transfer_id: 'TR-1', x: 10, y: 20, progress: 0.5 } }))
    expect(ev?.kind).toBe('courier_position')
  })
})

describe('SENT vs CONFIRMED', () => {
  it('keeps a sent action as SENT until evidence arrives', () => {
    const out = entry()
    expect(verificationFor(out, [out], [])).toBe('sent')
    expect(outcomeView('sent').tone).not.toBe('ok')
  })

  it('confirms only with a matching scan or verification entry', () => {
    const out = entry()
    expect(verificationFor(out, [out], [scan()])).toBe('confirmed')
    expect(verificationFor(out, [out, entry({ _id: 'v1', entry_type: 'verification', status: 'confirmed' })], [])).toBe('confirmed')
    expect(verificationFor(out, [out], [scan({ correlation_id: 'other' })])).toBe('sent')
    expect(outcomeView('confirmed').tone).toBe('ok')
  })

  it('surfaces a mismatch from the verifier', () => {
    const out = entry()
    expect(verificationFor(out, [out, entry({ _id: 'v2', entry_type: 'verification', status: 'mismatch' })], [])).toBe('mismatch')
  })
})

describe('approval permissions', () => {
  const approval = (over: Partial<Approval> = {}): Approval => ({
    _id: 'APR-0012',
    transfer_id: 'TR-0032',
    sample_id: 'EV-2201',
    requested_for_tool: 'checkout_sample',
    reason_requested: 'forensic_requires_supervisor',
    approver_role: 'supervisor',
    status: 'pending',
    ...over,
  })
  const [tech, supervisor] = IDENTITIES

  it('lets only a supervisor decide a pending approval', () => {
    expect(canDecideApproval(tech, approval())).toBe(false)
    expect(canDecideApproval(supervisor, approval())).toBe(true)
  })

  it('never lets anyone re-decide a finished approval', () => {
    expect(canDecideApproval(supervisor, approval({ status: 'approved' }))).toBe(false)
  })
})

describe('exposure countdown', () => {
  const start = '2026-11-14T19:00:00Z'
  const t0 = Date.parse(start)

  it('counts down from the verified checkout time', () => {
    expect(remainingSeconds(start, 3, t0)).toBe(180)
    expect(remainingSeconds(start, 3, t0 + 60_000)).toBe(120)
  })

  it('goes negative once the limit is exceeded', () => {
    expect(remainingSeconds(start, 3, t0 + 200_000)).toBe(-20)
    expect(formatClock(-20)).toBe('+0:20')
  })

  it('classifies safe / approaching / exceeded', () => {
    expect(exposureLevel(170, 3)).toBe('safe')
    expect(exposureLevel(50, 3)).toBe('approaching')
    expect(exposureLevel(0, 3)).toBe('exceeded')
  })

  it('returns NaN for a bad timestamp instead of a fake time', () => {
    expect(Number.isNaN(remainingSeconds('garbage', 3, t0))).toBe(true)
    expect(formatClock(75)).toBe('1:15')
  })
})

describe('exposure clock counts up', () => {
  const start = '2026-11-14T19:00:00Z'
  const t0 = Date.parse(start)

  it('counts elapsed time up from the verified checkout', () => {
    expect(elapsedSeconds(start, t0)).toBe(0)
    expect(elapsedSeconds(start, t0 + 65_000)).toBe(65)
    expect(formatClock(elapsedSeconds(start, t0 + 65_000))).toBe('1:05')
  })

  it('never goes negative if the scan time is slightly ahead of this clock', () => {
    expect(elapsedSeconds(start, t0 - 5_000)).toBe(0)
  })

  it('returns NaN for a bad timestamp rather than inventing a time', () => {
    expect(Number.isNaN(elapsedSeconds('garbage', t0))).toBe(true)
  })

  it('fills a bar toward the limit and reports remaining time', () => {
    const s = exposureStatus(30, 3) // 30 s of a 3 min limit
    expect(s.percent).toBeCloseTo(16.67, 1)
    expect(s.remaining).toBe(150)
    expect(s.level).toBe('safe')
    expect(s.overBy).toBe(0)
  })

  it('warns when nearing the limit', () => {
    expect(exposureStatus(130, 3).level).toBe('approaching')
  })

  it('keeps counting past the limit and says by how much, with the bar capped', () => {
    const s = exposureStatus(200, 3)
    expect(s.level).toBe('exceeded')
    expect(s.overBy).toBe(20)
    expect(s.percent).toBe(100)
    expect(formatClock(s.overBy)).toBe('0:20')
  })
})

describe('SSE deduplication', () => {
  const key = (e: LedgerEntry) => e._id
  const time = (e: LedgerEntry) => e.timestamp

  it('ignores replayed ids after a reconnect', () => {
    const a = entry({ _id: 'a' })
    const b = entry({ _id: 'b', timestamp: '2026-11-14T18:52:12Z' })
    const once = mergeById([], [a, b], key, time)
    expect(mergeById(once, [a, b], key, time)).toBe(once)
    expect(once).toHaveLength(2)
  })

  it('keeps chronological order when a snapshot back-fills older rows', () => {
    const newer = entry({ _id: 'n', timestamp: '2026-11-14T19:00:00Z' })
    const older = entry({ _id: 'o', timestamp: '2026-11-14T18:00:00Z' })
    expect(mergeById([newer], [older], key, time).map((e) => e._id)).toEqual(['o', 'n'])
  })

  it('derives a stable key for scans without _id', () => {
    expect(scanKey(scan())).toBe(scanKey(scan()))
    expect(scanKey(scan({ scanned_at: '2026-11-14T19:00:00Z' }))).not.toBe(scanKey(scan()))
  })
})

describe('stop request states', () => {
  it('starts idle, goes to requested, and clears on a halt entry or revoked transfer', async () => {
    const { useCustodyStore } = await import('../store/custodyStore')
    const s = useCustodyStore
    expect(s.getState().stopPhase).toBe('idle')
    s.getState().setStopPhase('requested', 'TR-0031')
    expect(s.getState().stopPhase).toBe('requested')
    s.getState().applyEvent({ kind: 'ledger', data: entry({ _id: 'h1', entry_type: 'decision', decision: 'halt' }) })
    expect(s.getState().stopPhase).toBe('idle')

    s.getState().setStopPhase('requested', 'TR-0031')
    s.getState().applyEvent({
      kind: 'transfer',
      data: { _id: 'TR-0031', sample_id: 'TS-1042', from: 'FZ-B', to: 'SEQ-3', requested_by: 'user:jlee', grant_id: 'GRANT-0007', state: 'revoked' },
    })
    expect(s.getState().stopPhase).toBe('idle')
    expect(s.getState().transfers['TR-0031'].state).toBe('revoked')
  })

  it('does not move a sample on a "sent" outcome, only on a scan', async () => {
    const { useCustodyStore, verifiedLocation } = await import('../store/custodyStore')
    const st = useCustodyStore.getState()
    st.applyEvent({ kind: 'ledger', data: entry({ _id: 'sent1', correlation_id: 'c-1' }) })
    expect(verifiedLocation('TS-1042', useCustodyStore.getState().scans, 'FZ-B').location).toBe('FZ-B')
    st.applyEvent({ kind: 'scan', data: scan({ type: 'handoff', location_id: 'CORR-1', correlation_id: 'c-1' }) })
    expect(verifiedLocation('TS-1042', useCustodyStore.getState().scans, 'FZ-B').location).toBe('CORR-1')
  })
})

describe('exposure clock', () => {
  it('runs from checkout until the sample is delivered or checked back in', async () => {
    const { activeExposure } = await import('../store/custodyStore')
    const out = scan({ scanned_at: '2026-11-14T18:00:00Z' })
    const handoff = scan({ type: 'handoff', location_id: 'CORR-1', scanned_at: '2026-11-14T18:01:00Z' })
    expect(activeExposure('TS-1042', [out, handoff])).toEqual({ checkedOutAt: '2026-11-14T18:00:00Z' })
    expect(activeExposure('TS-1042', [out, handoff, scan({ type: 'intake', location_id: 'SEQ-3', scanned_at: '2026-11-14T18:03:00Z' })])).toBeNull()
    expect(activeExposure('TS-1042', [out, scan({ type: 'checkin', scanned_at: '2026-11-14T18:02:00Z' })])).toBeNull()
    expect(activeExposure('TS-1077', [out])).toBeNull()
  })
})

describe('voice fallback behaviour', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reports a network failure as an ApiError so the UI can point at the typed box', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('failed')))
    await expect(sendVoiceRequest(new Blob(['x']), 'user:jlee')).rejects.toMatchObject({ kind: 'network' })
  })

  it('typed requests return the same intake shape and reject malformed bodies', async () => {
    const ok = { request_id: 'VR-1', transcript: 'Move TS-1042', parsed: { sample_id: 'TS-1042', from: 'FZ-B', to: 'SEQ-3' }, low_confidence_terms: [], transfer_id: 'TR-1' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(ok), { status: 200 })))
    await expect(sendTypedRequest('Move TS-1042', 'user:jlee')).resolves.toMatchObject({ transfer_id: 'TR-1' })

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ nope: 1 }), { status: 200 })))
    await expect(sendTypedRequest('x', 'user:jlee')).rejects.toBeInstanceOf(ApiError)
  })
})

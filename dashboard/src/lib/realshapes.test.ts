// The dashboard against the shapes the REAL backend sends (agent/events.py, agent/confirmation.py,
// agent/parse.py, agent/audit.py), as found by running the backend's route code.
import { describe, expect, it } from 'vitest'
import { resolveCourier } from './courier'
import { findingsFrom } from './findings'
import { alternativesFrom, needsClarification, problemFields, problemMessages } from './intake'
import { parseStreamEvent } from './normalize'
import { pointAt } from './route'
import { requestsFromTrace, transfersMissingRequests } from './requests'
import { SEED_LOCATIONS } from './seed'
import type { AuditResult, CourierPosition, IntakeResponse } from './types'
import { useCustodyStore } from '../store/custodyStore'

describe('read-back event from the real ConfirmationService', () => {
  const wire = { kind: 'confirmation_request', data: { id: 'CONF-1', transfer_id: 'TR-0001', text: "I'll check out TS-1077 from Freezer A and send it to Sequencing Station 3, arriving about 2:04. Confirm?" } }

  it('exposes the backend\'s `text` as the read-back sentence instead of dropping the event', () => {
    const ev = parseStreamEvent(JSON.stringify(wire))
    expect(ev?.kind).toBe('confirmation_request')
    if (ev?.kind === 'confirmation_request') {
      expect(ev.data.readback_text).toContain("I'll check out TS-1077")
      expect(ev.data.transfer_id).toBe('TR-0001')
      expect(ev.data.id).toBe('CONF-1')
    }
  })

  it('still accepts the older `readback_text` shape', () => {
    const ev = parseStreamEvent(JSON.stringify({ kind: 'confirmation_request', data: { transfer_id: 'T', readback_text: 'hello' } }))
    expect(ev && ev.kind === 'confirmation_request' && ev.data.readback_text).toBe('hello')
  })

  it('drops one with no sentence at all', () => {
    expect(parseStreamEvent(JSON.stringify({ kind: 'confirmation_request', data: { transfer_id: 'T' } }))).toBeNull()
  })
})

describe('courier position from the real courier_position event', () => {
  const outbound = (progress: number) => ({ job_id: 'CJ-208', transfer_id: 'TR-1', sample_id: 'TS-1077', from: 'FZ-A', to: 'SEQ-3', progress })
  const parse = (data: object) => {
    const ev = parseStreamEvent(JSON.stringify({ kind: 'courier_position', data }))
    if (ev?.kind !== 'courier_position') throw new Error('not parsed')
    return ev.data
  }

  it('parses a progress-only event instead of dropping it for lacking coordinates', () => {
    expect(parse(outbound(0.4)).progress).toBe(0.4)
  })

  it('still rejects an event with neither coordinates nor a leg', () => {
    expect(parseStreamEvent(JSON.stringify({ kind: 'courier_position', data: { transfer_id: 'T', progress: 0.5 } }))).toBeNull()
  })

  it('places the courier along the room -> corridor -> room route by progress', () => {
    const start = resolveCourier(parse(outbound(0)), SEED_LOCATIONS)!
    const end = resolveCourier(parse(outbound(1)), SEED_LOCATIONS)!
    const mid = resolveCourier(parse(outbound(0.5)), SEED_LOCATIONS)!
    expect([start.x, start.y]).toEqual([110, 90])
    expect([end.x, end.y]).toEqual([650, 90])
    expect([mid.x, mid.y]).toEqual([380, 210]) // 390 of 780 along: down 120, then 270 along the corridor
  })

  it('places the courier on the RECOVERY leg, where the backend reports from === to', () => {
    const out = resolveCourier(parse(outbound(0.5)), SEED_LOCATIONS)! // last drawn at (380, 210)
    const back = (p: number) => ({ ...outbound(p), to: 'FZ-A' }) // reroute to origin: from = to = FZ-A, progress restarts
    const a = resolveCourier(parse(back(0)), SEED_LOCATIONS, out)!
    const b = resolveCourier(parse(back(0.5)), SEED_LOCATIONS, a)!
    const c = resolveCourier(parse(back(1)), SEED_LOCATIONS, b)!
    expect([a.x, a.y]).toEqual([380, 210]) // starts where it was
    expect([b.x, b.y]).toEqual([185, 210]) // halfway: 195 of 390 back along the corridor
    expect([c.x, c.y]).toEqual([110, 90]) // arrives at the freezer
    expect(b.recoveryStart).toEqual({ x: 380, y: 210 }) // remembered, so the leg doesn't drift
  })

  it('does not invent a position for a recovery leg it never saw start', () => {
    const e = parse({ ...outbound(0.3), to: 'FZ-A' })
    expect(resolveCourier(e, SEED_LOCATIONS)).toBeNull()
  })

  it('keeps the last known position when a location is unknown', () => {
    const prev: CourierPosition = { transfer_id: 'TR-1', x: 1, y: 2, progress: 0.1 }
    expect(resolveCourier(parse({ ...outbound(0.5), to: 'NOWHERE' }), SEED_LOCATIONS, prev)).toBe(prev)
  })

  it('passes coordinates through when a backend sends them', () => {
    const p = resolveCourier(parse({ transfer_id: 'T', x: 5, y: 6, progress: 0.2 }), SEED_LOCATIONS)!
    expect([p.x, p.y]).toEqual([5, 6])
  })

  it('moves the courier in the store from real-shaped events', () => {
    const s = useCustodyStore.getState()
    s.applyEvent({ kind: 'courier_position', data: parse(outbound(0.5)) })
    expect(useCustodyStore.getState().courier['TR-1']).toMatchObject({ x: 380, y: 210 })
  })

  it('pointAt clamps and measures by path length', () => {
    const route = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }]
    expect(pointAt(route, 0.5)).toEqual({ x: 10, y: 0 })
    expect(pointAt(route, 2)).toEqual({ x: 10, y: 10 })
    expect(pointAt(route, -1)).toEqual({ x: 0, y: 0 })
  })
})

describe('clarification from the real intake `problems`', () => {
  const unknown: IntakeResponse = {
    request_id: 'VR-1',
    transcript: 'Move TS-1024 from Freezer B to Sequencing Station 3.',
    parsed: { sample_id: 'TS-1024', from: 'FZ-B', to: 'SEQ-3' },
    low_confidence_terms: [],
    transfer_id: null,
    problems: [{ field: 'sample_id', value: 'TS-1024', reason: 'unknown_sample', suggestions: ['TS-1042', 'TS-1077'], message: "I can't find TS-1024. Did you mean TS-1042 or TS-1077?" }],
  }

  it('turns the backend\'s suggestions into "did you mean" options', () => {
    expect(alternativesFrom(unknown)).toEqual([{ field: 'sample_id', options: ['TS-1042', 'TS-1077'] }])
  })

  it('shows the backend\'s own sentence and flags the field it could not validate', () => {
    expect(problemMessages(unknown)).toEqual(["I can't find TS-1024. Did you mean TS-1042 or TS-1077?"])
    expect([...problemFields(unknown)]).toEqual(['sample_id'])
  })

  it('needs clarification when there are problems, even though parsed has every field', () => {
    expect(needsClarification(unknown)).toBe(true)
  })

  it('is understood when there are no problems and a transfer was created', () => {
    expect(needsClarification({ ...unknown, problems: [], transfer_id: 'TR-1' })).toBe(false)
  })

  it('ignores problems with no suggestions or on unknown fields', () => {
    const r: IntakeResponse = { ...unknown, problems: [{ field: 'window', value: null, reason: 'missing', suggestions: [], message: 'x' }, { field: 'to', value: null, reason: 'missing', suggestions: [], message: 'y' }] }
    expect(alternativesFrom(r)).toEqual([])
  })
})

describe('a request the backend could not understand at all', () => {
  // agent/intake.py sends only {field, value, reason}: no message, no suggestions
  const unclear = {
    request_id: 'VR-2',
    transcript: 'asdf',
    parsed: {},
    low_confidence_terms: [],
    transfer_id: null,
    problems: [{ field: 'request', value: null, reason: 'not_understood' }],
  } as unknown as IntakeResponse

  it('does not crash on a problem with no suggestions', () => {
    expect(alternativesFrom(unclear)).toEqual([])
  })

  it('still tells the person what to do, in plain words', () => {
    expect(problemMessages(unclear)[0]).toContain('I didn')
    expect(problemMessages(unclear)[0]).toContain('Move TS-1042')
  })

  it('needs clarification', () => {
    expect(needsClarification(unclear)).toBe(true)
  })
})

describe('audit summary rows from the real pipelines', () => {
  const base: AuditResult = { entries: [], answer: '1 matching record(s).' }

  it('turns gap rows (which have no entry_type) into findings instead of an empty list', () => {
    const f = findingsFrom({ ...base, route: 'gaps', gaps: [{ _id: 'a', transfer_id: 'TR-0031', sample_id: 'TS-1077', tool: 'checkout_sample', timestamp: '2026-11-14T18:52:11+00:00', on_behalf_of: 'user:jlee', correlation_id: 'c-9', summary: 'checkout sent' }] })
    expect(f).toHaveLength(1)
    expect(f[0]).toMatchObject({ kind: 'gap', title: 'Check out sample', sampleId: 'TS-1077', tone: 'bad' })
    expect(f[0].facts.map(([k]) => k)).toEqual(expect.arrayContaining(['Sample', 'Transfer', 'Requested by', 'Sent at']))
  })

  it('turns per-sample exposure aggregates into findings', () => {
    const f = findingsFrom({ ...base, route: 'exposure_report', rows: [{ _id: 'TS-1077', total_minutes_out: 2.7, transfers: ['TR-0031'], excursions: 0 }, { _id: 'EV-2201', total_minutes_out: 25, transfers: ['TR-0032'], excursions: 1 }] })
    expect(f.map((x) => [x.title, x.tone])).toEqual([['TS-1077', 'ok'], ['EV-2201', 'bad']])
  })

  it('shows nothing extra when full ledger entries are present, or there is no data', () => {
    expect(findingsFrom(null)).toEqual([])
    expect(findingsFrom({ ...base, route: 'gaps', gaps: [] })).toEqual([])
  })
})

describe('original request from a transfer trace', () => {
  const req = { _id: 'VR-0019', source: 'voice', speaker_id: 'user:jlee', transcript: 'Move TS-1077.', created_at: '2026-11-14T18:50:02+00:00' }

  it('extracts each request once, ignoring rows without one', () => {
    const docs = requestsFromTrace([{ request: req }, { request: req }, { tool: 'x' }, { request: { _id: 5 } }])
    expect(docs.map((d) => d._id)).toEqual(['VR-0019'])
  })

  it('returns nothing for a non-list', () => {
    expect(requestsFromTrace({ error: 'x' })).toEqual([])
  })

  it('asks only for transfers whose request we lack, capped', () => {
    const entries = [
      { transfer_id: 'TR-1', request_id: 'VR-1' },
      { transfer_id: 'TR-1', request_id: 'VR-1' },
      { transfer_id: 'TR-2', request_id: 'VR-2' },
      { transfer_id: 'TR-3', request_id: null },
    ]
    expect(transfersMissingRequests(entries, [{ _id: 'VR-2' }])).toEqual(['TR-1'])
    expect(transfersMissingRequests(entries, [], 1)).toEqual(['TR-1'])
  })
})

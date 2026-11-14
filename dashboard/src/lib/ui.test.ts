import { describe, expect, it } from 'vitest'
import { groupLedger } from './actions'
import { plannedRoute, recoveryRoute, routeLength, routeUpTo } from './route'
import type { LedgerEntry, ScanEvent } from './types'

let n = 0
const entry = (over: Partial<LedgerEntry> = {}): LedgerEntry => ({
  _id: `e${++n}`,
  timestamp: `2026-11-14T18:52:${String(10 + n).padStart(2, '0')}Z`,
  entry_type: 'decision',
  transfer_id: 'TR-1',
  sample_id: 'TS-1042',
  correlation_id: 'c-1',
  actor: 'agent:custody-01',
  on_behalf_of: 'user:jlee',
  policy_id: 'custody-default',
  policy_version: 3,
  decision: 'allow',
  tool: 'checkout_sample',
  reversible: false,
  ...over,
})
const scan = (over: Partial<ScanEvent> = {}): ScanEvent => ({ type: 'checkout', sample_id: 'TS-1042', location_id: 'FZ-B', correlation_id: 'c-1', scanned_at: '2026-11-14T18:53:00Z', ...over })

describe('ledger grouping', () => {
  it('collapses decision, outcome and verification of one tool call into a single action', () => {
    const rows = [entry(), entry({ entry_type: 'outcome', status: 'sent' }), entry({ entry_type: 'verification', status: 'confirmed' })]
    const items = groupLedger(rows, [scan()])
    expect(items).toHaveLength(1)
    expect(items[0].verified).toBe(true)
    expect(items[0].status).toBe('Verified by scanner')
    expect(items[0].steps.map((s) => s.state)).toEqual(['done', 'done', 'done'])
  })

  it('does not call a sent action verified without scanner evidence', () => {
    const items = groupLedger([entry(), entry({ entry_type: 'outcome', status: 'sent' })], [])
    expect(items[0].verified).toBe(false)
    expect(items[0].status).toBe('Sent, awaiting scan')
    expect(items[0].verification).toBe('sent')
    expect(items[0].steps.map((s) => s.state)).toEqual(['done', 'done', 'active'])
  })

  it('marks later steps as not run when the gate did not allow the action', () => {
    const items = groupLedger([entry({ decision: 'pending_approval' })], [])
    expect(items[0].status).toBe('Needs approval')
    expect(items[0].tone).toBe('warn')
    expect(items[0].steps.map((s) => s.state)).toEqual(['active', 'skipped', 'skipped'])
  })

  it('treats a reversible service call as done without needing a scan', () => {
    const rows = [entry({ tool: 'reserve_station', reversible: true }), entry({ tool: 'reserve_station', reversible: true, entry_type: 'outcome', status: 'confirmed' })]
    const items = groupLedger(rows, [])
    expect(items[0].physical).toBe(false)
    expect(items[0].steps).toHaveLength(2)
    expect(items[0].status).toBe('Done')
  })

  it('keeps a verification-only recovery step as done, and non-tool events as separate rows', () => {
    const rows = [entry({ entry_type: 'intake', tool: null, correlation_id: 'c-0' }), entry({ entry_type: 'verification', status: 'confirmed', tool: 'return_to_storage', correlation_id: 'c-9' })]
    const items = groupLedger(rows, [])
    expect(items.map((i) => i.kind)).toEqual(['event', 'action'])
    expect(items[1].steps[1].state).toBe('done')
  })

  it('surfaces a halt as its own bad-toned event', () => {
    const items = groupLedger([entry({ decision: 'halt', tool: null, entry_type: 'decision' })], [])
    expect(items[0].title).toBe('Agent halted')
    expect(items[0].tone).toBe('bad')
  })
})

describe('map routes', () => {
  const a = { x: 110, y: 90 }
  const b = { x: 650, y: 90 }
  const corridor = { x: 380, y: 210 }

  it('runs room → corridor lane → room at right angles', () => {
    expect(plannedRoute(a, b, corridor)).toEqual([a, { x: 110, y: 210 }, { x: 650, y: 210 }, b])
  })

  it('drops duplicate points when a room sits on the lane', () => {
    expect(plannedRoute({ x: 110, y: 210 }, b, corridor)).toHaveLength(3)
  })

  it('draws only the part of the route the courier has covered', () => {
    const route = plannedRoute(a, b, corridor)
    const pos = { x: 300, y: 210 }
    const done = routeUpTo(route, pos)
    expect(done[done.length - 1]).toEqual(pos)
    expect(done).toHaveLength(3)
    expect(routeUpTo(route, { x: 999, y: 999 })).toEqual([])
  })

  it('returns to the freezer via the corridor from a point mid-lane or on a vertical leg', () => {
    const back = { x: 110, y: 90 }
    expect(recoveryRoute({ x: 300, y: 210 }, back, corridor)).toEqual([{ x: 300, y: 210 }, { x: 110, y: 210 }, back])
    expect(recoveryRoute({ x: 650, y: 150 }, back, corridor)).toEqual([{ x: 650, y: 150 }, { x: 650, y: 210 }, { x: 110, y: 210 }, back])
    expect(routeLength([a, { x: 110, y: 210 }])).toBe(120)
  })
})

// Person B's guardrails (GUARDRAILS_SPEC.md) and courier reroute fields, as the real backend writes them
// (agent/guardrails.py::abort_entry, agent/events.py::CourierTicker).
import { describe, expect, it } from 'vitest'
import { groupLedger } from './actions'
import { authorityFromLedger } from './authority'
import { resolveCourier } from './courier'
import { parseStreamEvent } from './normalize'
import { explainRule } from './rules'
import { explainRunEnd, isRunEnd, RUN_END_STATUSES, runEndFor, runEndStatus } from './runEnd'
import { SEED_LOCATIONS } from './seed'
import type { LedgerEntry } from './types'

let n = 0
const row = (over: Partial<LedgerEntry> = {}): LedgerEntry => ({
  _id: `g${++n}`,
  timestamp: `2026-11-14T19:${String(10 + n).padStart(2, '0')}:00Z`,
  entry_type: 'decision',
  transfer_id: 'TR-1',
  sample_id: 'TS-1042',
  correlation_id: `c-${n}`,
  actor: 'agent:custody-01',
  on_behalf_of: 'user:jlee',
  grant_id: 'GRANT-0007',
  policy_id: 'custody-default',
  policy_version: 3,
  decision: 'allow',
  ...over,
})
/** Exactly what agent/guardrails.py::abort_entry writes. */
const abort = (status: string, over: Partial<LedgerEntry> = {}): LedgerEntry =>
  row({ entry_type: 'violation', actor: 'system:guardrail', policy_id: 'guardrails', policy_version: 1, decision: 'halt', tool: null, rule_hits: [status], reason: 'no result after 12 model steps', summary: `The run for TR-1 ended early (${status}): no result after 12 model steps`, ...over })

describe('guardrail refusals are explained in plain language', () => {
  const NAMES = ['unknown_tool', 'invalid_arguments', 'args_not_bound_to_transfer', 'duplicate_irreversible_step', 'gate_unavailable']

  it.each(NAMES)('%s has a title, a reason and a next step, and is marked as a guardrail', (rule) => {
    const e = explainRule(rule)
    expect(e.kind).toBe('guardrail')
    expect(e.title.length).toBeGreaterThan(10)
    expect(e.why.length).toBeGreaterThan(20)
    expect(e.next.length).toBeGreaterThan(10)
    expect(e.title).not.toContain(rule) // the raw rule name is shown separately, not as the explanation
  })

  it('says nothing ran, or that nothing was done twice, so the person is not alarmed', () => {
    expect(explainRule('unknown_tool').next).toMatch(/Nothing was changed/)
    expect(explainRule('duplicate_irreversible_step').next).toMatch(/Nothing was done twice/)
  })

  it('still treats ordinary policy rules as policy', () => {
    expect(explainRule('destination_not_certified').kind).toBeUndefined()
  })
})

describe('a run that ended abnormally is not a policy decision', () => {
  it.each(RUN_END_STATUSES)('%s is recognised from the row the backend writes, and explained as a failure', (status) => {
    const e = abort(status)
    expect(isRunEnd(e)).toBe(true)
    expect(runEndStatus(e)).toBe(status)
    const why = explainRunEnd(status)
    expect(why.kind).toBe('failure')
    expect(why.next).toMatch(/rolled back/)
  })

  it('is NOT confused with a watcher halt (grant revoked / Stop) or a violation from someone else', () => {
    expect(isRunEnd(row({ decision: 'halt', actor: 'system:watcher', rule_hits: ['grant_revoked'] }))).toBe(false)
    expect(isRunEnd(abort('budget', { actor: 'agent:custody-01' }))).toBe(false)
    expect(isRunEnd(abort('something_else'))).toBe(false)
  })

  it('shows as "Run ended early" (a warning), not as "Agent halted"', () => {
    const [item] = groupLedger([abort('budget')], [])
    expect(item.title).toBe('Run ended early')
    expect(item.tone).toBe('warn')
    const [halt] = groupLedger([row({ decision: 'halt', actor: 'system:watcher', tool: null, rule_hits: ['grant_revoked'] })], [])
    expect(halt.title).toBe('Agent halted')
  })

  it('does not count as the agent losing its authority', () => {
    expect(authorityFromLedger([abort('budget'), abort('llm_error')]).revoked).toBe(false)
  })

  it('finds the latest ending for a transfer, and none for another', () => {
    const rows = [abort('budget'), row({ transfer_id: 'TR-2' }), abort('llm_error')]
    expect(runEndFor(rows, 'TR-1')?.rule_hits).toEqual(['llm_error'])
    expect(runEndFor(rows, 'TR-2')).toBeNull()
    expect(runEndFor(rows, null)).toBeNull()
  })

  it('falls back gracefully for a status it has never seen', () => {
    expect(explainRunEnd('brand_new').title).toBe('The agent stopped early')
  })
})

describe('courier after a reroute home carries original_to and return_from_progress', () => {
  const parse = (data: object) => {
    const ev = parseStreamEvent(JSON.stringify({ kind: 'courier_position', data }))
    if (ev?.kind !== 'courier_position') throw new Error('not parsed')
    return ev.data
  }
  // exactly the payload agent/events.py::CourierTicker publishes after a reroute home
  const home = (progress: number, extra: object = { original_to: 'SEQ-3', return_from_progress: 0.5 }) => ({
    job_id: 'CJ-208', transfer_id: 'TR-1', sample_id: 'TS-1077', from: 'FZ-A', to: 'FZ-A', progress, ...extra,
  })

  it('keeps both new fields when parsing', () => {
    const d = parse(home(0.3))
    expect(d.original_to).toBe('SEQ-3')
    expect(d.return_from_progress).toBe(0.5)
  })

  it('places the recovery leg WITHOUT having watched the outbound leg (a reload mid-recovery)', () => {
    const start = resolveCourier(parse(home(0)), SEED_LOCATIONS)! // no previous position at all
    const mid = resolveCourier(parse(home(0.5)), SEED_LOCATIONS)!
    const end = resolveCourier(parse(home(1)), SEED_LOCATIONS)!
    expect([start.x, start.y]).toEqual([380, 210]) // it turned back half way along the outbound route
    expect([mid.x, mid.y]).toEqual([185, 210])
    expect([end.x, end.y]).toEqual([110, 90]) // back at Freezer A
  })

  it('trusts the backend over what it remembers', () => {
    const wrong = { transfer_id: 'TR-1', x: 650, y: 90, progress: 0.9, recoveryStart: { x: 650, y: 90 } }
    const p = resolveCourier(parse(home(0)), SEED_LOCATIONS, wrong)!
    expect([p.x, p.y]).toEqual([380, 210])
  })

  it('clamps an out-of-range return_from_progress', () => {
    const p = resolveCourier(parse(home(0, { original_to: 'SEQ-3', return_from_progress: 7 })), SEED_LOCATIONS)!
    expect([p.x, p.y]).toEqual([650, 90]) // the very end of the outbound route
  })

  it('still works on an older backend that does not send them', () => {
    const out = resolveCourier(parse({ job_id: 'J', transfer_id: 'TR-1', from: 'FZ-A', to: 'SEQ-3', progress: 0.5 }), SEED_LOCATIONS)!
    const p = resolveCourier(parse(home(0.5, {})), SEED_LOCATIONS, out)!
    expect([p.x, p.y]).toEqual([185, 210])
  })
})

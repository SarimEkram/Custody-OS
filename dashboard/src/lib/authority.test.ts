import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, reissueGrant } from './api'
import { groupLedger } from './actions'
import { authorityFromLedger, denialFor } from './authority'
import { explainRule, GRANT_RULES } from './rules'
import type { LedgerEntry, ScanEvent } from './types'

let n = 0
const entry = (over: Partial<LedgerEntry> = {}): LedgerEntry => ({
  _id: `e${++n}`,
  timestamp: `2026-11-14T18:${String(10 + n).padStart(2, '0')}:00Z`,
  entry_type: 'decision',
  transfer_id: 'TR-1',
  sample_id: 'TS-1077',
  correlation_id: `c-${n}`,
  actor: 'agent:custody-01',
  on_behalf_of: 'user:jlee',
  grant_id: 'GRANT-0007',
  policy_id: 'custody-default',
  policy_version: 3,
  decision: 'allow',
  tool: 'lookup_sample',
  ...over,
})
const halt = (rule = 'grant_revoked') => entry({ decision: 'halt', tool: null, actor: 'system:watcher', rule_hits: [rule], reason: `grant revoked by jlee` })

describe('authority from the ledger', () => {
  it('has authority with an empty ledger or normal activity', () => {
    expect(authorityFromLedger([]).revoked).toBe(false)
    expect(authorityFromLedger([entry(), entry({ tool: 'reserve_station' })]).revoked).toBe(false)
  })

  it('is revoked after a grant halt', () => {
    const a = authorityFromLedger([entry(), halt()])
    expect(a.revoked).toBe(true)
    expect(a.grantId).toBe('GRANT-0007')
    expect(a.reason).toContain('revoked')
  })

  it('is revoked after a grant-related denial, with no halt', () => {
    expect(authorityFromLedger([entry({ decision: 'deny', rule_hits: ['grant_expired'] })]).revoked).toBe(true)
  })

  it('is NOT revoked by the Stop switch: Stop halts transfers, not the grant', () => {
    expect(authorityFromLedger([entry(), halt('stop_switch')]).revoked).toBe(false)
  })

  it('ignores recovery/system actions that happen after the halt', () => {
    const rec = entry({ actor: 'system:recovery', policy_id: 'recovery', tool: 'cancel_reservation' })
    expect(authorityFromLedger([entry(), halt(), rec]).revoked).toBe(true)
  })

  it('regains authority once the gate allows the agent to act again', () => {
    expect(authorityFromLedger([halt(), entry({ tool: 'reserve_station' })]).revoked).toBe(false)
  })

  it('ignores an unrelated denial (the grant is fine)', () => {
    expect(authorityFromLedger([entry({ decision: 'deny', rule_hits: ['destination_not_certified'] })]).revoked).toBe(false)
  })
})

describe('denial for a transfer', () => {
  const none: ScanEvent[] = []

  it('finds the refusal and its rules', () => {
    const d = denialFor([entry({ decision: 'deny', rule_hits: ['grant_revoked'] })], none, 'TR-1')
    expect(d?.rules).toEqual(['grant_revoked'])
    expect(d?.nothingPhysical).toBe(true)
  })

  it('returns null when nothing was denied, or no transfer is given', () => {
    expect(denialFor([entry()], none, 'TR-1')).toBeNull()
    expect(denialFor([entry({ decision: 'deny' })], none, null)).toBeNull()
  })

  it('does not claim "nothing physical" if a scan followed the request', () => {
    const rows = [entry({ decision: 'deny', rule_hits: ['x'] })]
    const scan: ScanEvent = { type: 'checkout', sample_id: 'TS-1077', location_id: 'FZ-A', scanned_at: '2099-01-01T00:00:00Z' }
    expect(denialFor(rows, [scan], 'TR-1')?.nothingPhysical).toBe(false)
  })
})

describe('rule explanations', () => {
  it('explains every grant rule with a next step', () => {
    for (const r of GRANT_RULES) {
      const e = explainRule(r)
      expect(e.title.length).toBeGreaterThan(5)
      expect(e.next.length).toBeGreaterThan(5)
    }
  })

  it('explains a precondition rule from its name', () => {
    expect(explainRule('precondition:station_reserved').why).toContain('station reserved')
  })

  it('falls back gracefully for an unknown rule, still showing it', () => {
    expect(explainRule('brand_new_rule').title).toContain('brand new rule')
  })
})

describe('restoring authority', () => {
  const reissue = () => entry({ entry_type: 'correction', decision: 'allow', tool: null, actor: 'user:mokafor', rule_hits: ['grant_reissued'], grant_id: 'GRANT-0008' })

  it('a recorded re-issue ends the revoked state', () => {
    expect(authorityFromLedger([halt(), reissue()]).revoked).toBe(false)
  })

  it('a revoke AFTER a re-issue revokes again (latest evidence wins)', () => {
    expect(authorityFromLedger([halt(), reissue(), halt()]).revoked).toBe(true)
  })

  it('shows the re-issue as "Authority restored", not a generic correction', () => {
    const items = groupLedger([reissue()], [])
    expect(items[0].title).toBe('Authority restored')
    expect(items[0].tone).toBe('ok')
  })
})

describe('reissueGrant request', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends only who is asking and why: the client can never choose scope or expiry', async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, grant_id: 'GRANT-0008', supersedes: 'GRANT-0007', expires_at: '2026-11-14T20:00:00Z' }), { status: 200 }))
    vi.stubGlobal('fetch', f)
    const r = await reissueGrant('GRANT-0007', 'user:mokafor', 'Revoked by mistake')
    expect(r.grant_id).toBe('GRANT-0008')
    const [url, init] = f.mock.calls[0]
    expect(String(url)).toMatch(/\/grants\/GRANT-0007\/reissue$/)
    expect(JSON.parse(init.body)).toEqual({ by: 'user:mokafor', reason: 'Revoked by mistake' })
  })

  it("surfaces the server's refusal message (e.g. not a supervisor)", async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Only a supervisor can issue a new grant.' }), { status: 403 })))
    await expect(reissueGrant('GRANT-0007', 'user:jlee', 'please')).rejects.toMatchObject({ kind: 'http', status: 403, message: 'Only a supervisor can issue a new grant.' })
  })

  it('reports a backend without the endpoint as not_implemented', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 404 })))
    const err = await reissueGrant('GRANT-0007', 'user:mokafor', 'a reason here').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.kind).toBe('not_implemented')
  })
})

// Derives "can the agent act right now?" from evidence the backend already sent (ledger entries),
// never from a guess. The most recent relevant evidence wins.
import { GRANT_REISSUED, GRANT_RULES } from './rules'
import type { Grant, LedgerEntry, ScanEvent } from './types'

export interface Authority {
  revoked: boolean
  grantId: string | null
  reason: string | null
  since: string | null
}

const OK: Authority = { revoked: false, grantId: null, reason: null, since: null }

/** An action the agent itself was allowed to take. Recovery/system actions don't prove authority. */
const isAgentAllow = (e: LedgerEntry): boolean =>
  e.entry_type === 'decision' && e.decision === 'allow' && !!e.tool && e.actor.startsWith('agent:') && e.policy_id !== 'recovery'

/** The ledger entry written when a supervisor issues a replacement grant. */
const isReissue = (e: LedgerEntry): boolean => !!e.rule_hits?.includes(GRANT_REISSUED)

const isGrantFailure = (e: LedgerEntry): boolean =>
  (e.decision === 'halt' || e.decision === 'deny') && !!e.rule_hits?.some((r) => GRANT_RULES.includes(r))

export function authorityFromLedger(ledger: LedgerEntry[]): Authority {
  for (let i = ledger.length - 1; i >= 0; i--) {
    const e = ledger[i]
    if (isAgentAllow(e) || isReissue(e)) return OK // the gate let the agent act, or a supervisor issued a new grant
    if (isGrantFailure(e)) return { revoked: true, grantId: e.grant_id ?? null, reason: e.reason ?? e.summary ?? null, since: e.timestamp }
  }
  return OK
}

export interface Denial {
  entry: LedgerEntry
  rules: string[]
  /** True if nothing physical happened for this transfer (no scan after the request). */
  nothingPhysical: boolean
}

/** The gate's refusal for a transfer, if it was denied outright. */
export function denialFor(ledger: LedgerEntry[], scans: ScanEvent[], transferId: string | null | undefined): Denial | null {
  if (!transferId) return null
  const mine = ledger.filter((e) => e.transfer_id === transferId)
  const entry = [...mine].reverse().find((e) => e.entry_type === 'decision' && e.decision === 'deny')
  if (!entry) return null
  const sample = entry.sample_id
  const out = mine.some((e) => e.entry_type === 'outcome' && e.reversible === false)
  const scanned = !!sample && scans.some((s) => s.sample_id === sample && Date.parse(s.scanned_at) >= Date.parse(mine[0].timestamp))
  return { entry, rules: entry.rule_hits ?? [], nothingPhysical: !out && !scanned }
}

/**
 * Keep the grant list current from ledger evidence as it arrives (no reload, no new request needed):
 *  - a re-issue row: the new grant is active, the one it replaces stays revoked.
 *  - a grant_revoked halt: that grant is revoked.
 */
export function applyGrantEvidence(grants: Record<string, Grant>, e: LedgerEntry): Record<string, Grant> {
  if (isReissue(e) && e.grant_id) {
    const r = (e.response ?? {}) as { supersedes?: string; expires_at?: string }
    const next = { ...grants }
    if (r.supersedes) next[r.supersedes] = { ...(grants[r.supersedes] ?? { _id: r.supersedes, grantor: e.on_behalf_of, grantee: 'agent' }), status: 'revoked' }
    next[e.grant_id] = {
      ...(grants[e.grant_id] ?? { _id: e.grant_id, grantor: e.on_behalf_of, grantee: 'agent' }),
      status: 'active',
      supersedes: r.supersedes ?? null,
      ...(r.expires_at ? { expires_at: r.expires_at } : {}),
    }
    return next
  }
  if (e.decision === 'halt' && e.rule_hits?.includes('grant_revoked') && e.grant_id && grants[e.grant_id]?.status !== 'revoked') {
    return { ...grants, [e.grant_id]: { ...(grants[e.grant_id] ?? { _id: e.grant_id, grantor: e.on_behalf_of, grantee: 'agent' }), status: 'revoked' } }
  }
  return grants
}

/** The grant the agent is acting under now: the active one, if any. */
export const currentGrant = (grants: Record<string, Grant>): Grant | undefined =>
  Object.values(grants).filter((g) => g.status === 'active').sort((a, b) => (b.expires_at ?? '').localeCompare(a.expires_at ?? ''))[0]

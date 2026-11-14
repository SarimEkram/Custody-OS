import { useMemo } from 'react'
import { authorityFromLedger, type Authority } from '../lib/authority'
import { DEFAULT_GRANT_ID } from '../lib/seed'
import { useCustodyStore } from '../store/custodyStore'

/** Whether the agent currently has authority to act, derived from the ledger (and grant snapshot if provided). */
export function useAuthority(): Authority {
  const ledger = useCustodyStore((s) => s.ledger)
  const grants = useCustodyStore((s) => s.grants)
  const noGrantAt = useCustodyStore((s) => s.noGrantAt)
  const transfers = useCustodyStore((s) => s.transfers)
  return useMemo(() => {
    const fromLedger = authorityFromLedger(ledger)
    if (fromLedger.revoked) return fromLedger
    // The real backend refuses a request with 403 "no active grant" before writing anything, so there may be
    // no ledger row at all. The refusal itself is the evidence.
    if (noGrantAt) {
      const last = Object.values(transfers).at(-1)?.grant_id ?? DEFAULT_GRANT_ID
      return { revoked: true, grantId: last, reason: 'The backend reports no active grant', since: noGrantAt }
    }
    const all = Object.values(grants)
    if (all.some((x) => x.status === 'active')) return fromLedger // a current grant exists; revoked ones are history
    const g = all.find((x) => x.status === 'revoked')
    return g ? { revoked: true, grantId: g._id, reason: null, since: null } : fromLedger
  }, [ledger, grants, noGrantAt, transfers])
}

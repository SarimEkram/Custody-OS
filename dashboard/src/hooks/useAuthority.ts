import { useMemo } from 'react'
import { authorityFromLedger, type Authority } from '../lib/authority'
import { useCustodyStore } from '../store/custodyStore'

/** Whether the agent currently has authority to act, derived from the ledger (and grant snapshot if provided). */
export function useAuthority(): Authority {
  const ledger = useCustodyStore((s) => s.ledger)
  const grants = useCustodyStore((s) => s.grants)
  return useMemo(() => {
    const fromLedger = authorityFromLedger(ledger)
    if (fromLedger.revoked) return fromLedger
    const all = Object.values(grants)
    if (all.some((x) => x.status === 'active')) return fromLedger // a current grant exists; revoked ones are history
    const g = all.find((x) => x.status === 'revoked')
    return g ? { revoked: true, grantId: g._id, reason: null, since: null } : fromLedger
  }, [ledger, grants])
}

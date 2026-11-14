import { ShieldOff } from 'lucide-react'
import { useAuthority } from '../../hooks/useAuthority'
import { formatTime } from '../../lib/format'
import { useCustodyStore } from '../../store/custodyStore'
import { Mono } from '../ui/Badge'

/**
 * Shown while the agent has no valid authority. The app is not shut down: you can still review
 * activity and use the Auditor, and new requests are accepted but the gate denies and logs them.
 */
export function AuthorityBanner() {
  const authority = useAuthority()
  const setView = useCustodyStore((s) => s.setView)
  const setRestoreOpen = useCustodyStore((s) => s.setRestoreOpen)
  if (!authority.revoked) return null

  return (
    <div role="status" className="slot-in flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-bad/30 bg-bad/10 px-5 py-2.5">
      <ShieldOff size={18} className="shrink-0 text-bad" aria-hidden />
      <p className="min-w-0 flex-1 text-sm">
        <span className="font-semibold text-bad">
          {authority.grantId ? <Mono>{authority.grantId}</Mono> : 'The agent’s grant'} is no longer active
          {authority.since ? ` (since ${formatTime(authority.since)})` : ''}.
        </span>{' '}
        <span className="text-muted">The agent can’t act. New requests are still logged, but the gate will deny them. Activity and the Auditor stay available.</span>
      </p>
      <div className="flex items-center gap-2 text-sm">
        <button onClick={() => setView('auditor')} className="rounded-lg px-3 py-1.5 text-fg ring-1 ring-line hover:bg-panel">Open Auditor</button>
        <button onClick={() => setRestoreOpen(true)} className="rounded-lg bg-warn px-3 py-1.5 font-medium text-bg hover:brightness-110">Restore authority</button>
      </div>
    </div>
  )
}

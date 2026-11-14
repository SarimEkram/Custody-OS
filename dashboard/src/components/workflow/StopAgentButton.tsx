import { Loader2, OctagonX } from 'lucide-react'
import { useState } from 'react'
import { useAgentStatus } from '../../hooks/useAgentStatus'
import { currentGrant } from '../../lib/authority'
import { useAuthority } from '../../hooks/useAuthority'
import { ApiError, revokeGrant, stopAgent } from '../../lib/api'
import { pickActiveTransfer, useCustodyStore } from '../../store/custodyStore'
import { Mono } from '../ui/Badge'

/** Stop is requested here, but only the backend (a halt entry or the transfer state) confirms it took effect. */
export function StopAgentButton({ compact = false }: { compact?: boolean }) {
  const status = useAgentStatus()
  const transfer = useCustodyStore((s) => pickActiveTransfer(s))
  const identityId = useCustodyStore((s) => s.identityId)
  const phase = useCustodyStore((s) => s.stopPhase)
  const setStopPhase = useCustodyStore((s) => s.setStopPhase)

  const requested = phase === 'requested'
  const halted = transfer?.state === 'revoked' || transfer?.state === 'recovering'
  const stop = async () => {
    setStopPhase('requested', transfer?._id ?? null)
    try {
      await stopAgent(identityId, null)
    } catch (e) {
      setStopPhase('failed', transfer?._id ?? null, e instanceof ApiError ? e.message : 'Stop request failed.')
    }
  }

  return (
    <button
      onClick={() => void stop()}
      disabled={!status.operating || requested || halted}
      className={`flex items-center justify-center gap-2.5 rounded-xl bg-bad font-bold text-bg shadow-[0_6px_20px_-8px_rgba(240,107,120,0.8)] hover:brightness-110 ${
        compact ? 'h-10 w-full px-4 text-sm' : 'h-14 min-w-[168px] px-6 text-[17px]'
      }`}
    >
      {requested ? <Loader2 className="animate-spin" size={compact ? 16 : 22} /> : <OctagonX size={compact ? 16 : 22} aria-hidden />}
      {requested ? 'Stopping…' : 'Stop agent'}
    </button>
  )
}

/** One honest line about what the stop/halt/recovery has actually reached. Empty when nothing is happening. */
export function StopStatusLine() {
  const transfer = useCustodyStore((s) => pickActiveTransfer(s))
  const phase = useCustodyStore((s) => s.stopPhase)
  const stopError = useCustodyStore((s) => s.stopError)

  let tone = ''
  let text = ''
  if (phase === 'failed') {
    tone = 'text-bad'
    text = stopError ?? 'Stop request failed.'
  } else if (phase === 'requested') {
    tone = 'text-warn'
    text = 'Stop requested. Waiting for the backend to confirm the halt. A call already in flight may still finish.'
  } else if (transfer?.state === 'revoked') {
    tone = 'text-bad'
    text = 'Halt confirmed: the agent’s authority was revoked.'
  } else if (transfer?.state === 'recovering') {
    tone = 'text-warn'
    text = 'Recovery in progress: returning the sample to certified storage.'
  } else if (transfer?.state === 'recovered') {
    tone = 'text-ok'
    text = 'Recovery complete: the sample is back in storage.'
  } else if (transfer?.state === 'cancelled') {
    tone = 'text-muted'
    text = 'Transfer cancelled. Nothing irreversible had run.'
  }
  return (
    <p aria-live="polite" role={phase === 'failed' ? 'alert' : undefined} className={`text-[13px] ${tone} ${text ? '' : 'sr-only'}`}>
      {text}
    </p>
  )
}

export function GrantChip() {
  const transfer = useCustodyStore((s) => pickActiveTransfer(s))
  const grants = useCustodyStore((s) => s.grants)
  const identityId = useCustodyStore((s) => s.identityId)
  const authority = useAuthority()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Show the grant the agent acts under NOW (a re-issued one), not the one an earlier run used.
  const current = currentGrant(grants)
  const grantId = current?._id ?? transfer?.grant_id
  if (!grantId || !transfer) return null
  const grant = grants[grantId]
  const revoked = current ? false : grant?.status === 'revoked' || authority.revoked || ['revoked', 'recovering', 'recovered'].includes(transfer.state)

  const revoke = async () => {
    setPending(true)
    setError(null)
    try {
      await revokeGrant(grantId, identityId)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Revocation failed.')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="min-w-[132px]">
      <p className="mb-1.5 text-xs text-muted">Agent authority</p>
      <div className="flex items-center gap-2">
        <Mono className="text-[13px] font-medium">{grantId}</Mono>
        <span className={`text-xs font-medium ${revoked ? 'text-bad' : grant ? 'text-ok' : 'text-muted'}`}>{revoked ? 'Revoked' : grant ? 'Active' : 'Unknown'}</span>
      </div>
      <button onClick={() => void revoke()} disabled={pending || revoked} className="mt-1 text-xs font-medium text-bad underline-offset-2 hover:underline">
        {pending ? 'Revoking…' : 'Revoke grant'}
      </button>
      {error && <p role="alert" className="mt-1 text-xs text-bad">{error}</p>}
    </div>
  )
}

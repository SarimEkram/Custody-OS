import { CheckCircle2, Loader2, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useAuthority } from '../../hooks/useAuthority'
import { ApiError, reissueGrant } from '../../lib/api'
import { formatDateTime } from '../../lib/format'
import { IDENTITIES } from '../../lib/seed'
import type { ReissueResult } from '../../lib/types'
import { useCustodyStore } from '../../store/custodyStore'
import { Mono } from '../ui/Badge'
import { Drawer } from '../ui/Drawer'

const MIN_REASON = 8

/**
 * Restore the agent's authority by issuing a NEW grant. Deliberately narrow:
 * - It never reactivates the revoked grant (that stays revoked, on record).
 * - The person can't choose scope or expiry: the server copies the old scope (never wider) and sets a short expiry.
 * - A reason is required and is recorded in the ledger under the supervisor's name.
 * - Whether this person is allowed is decided by the backend. The check here only avoids a pointless request.
 */
export function RestoreAuthorityDialog() {
  const open = useCustodyStore((s) => s.restoreOpen)
  const setOpen = useCustodyStore((s) => s.setRestoreOpen)
  const identityId = useCustodyStore((s) => s.identityId)
  const setIdentity = useCustodyStore((s) => s.setIdentity)
  const setNoGrant = useCustodyStore((s) => s.setNoGrant)
  const authority = useAuthority()
  const me = IDENTITIES.find((i) => i._id === identityId) ?? IDENTITIES[0]
  const isSupervisor = me.role === 'supervisor'
  const holder = IDENTITIES.find((i) => i.role === 'lab_tech')

  const [reason, setReason] = useState('')
  const [ack, setAck] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ReissueResult | null>(null)

  // Never leave the button spinning if the backend doesn't answer.
  useEffect(() => {
    if (!pending) return
    const t = setTimeout(() => {
      setPending(false)
      setError('The backend has not responded yet. Nothing was issued. Check the Activity list, then try again.')
    }, 8000)
    return () => clearTimeout(t)
  }, [pending])

  const close = () => {
    setOpen(false)
    setError(null)
    if (result) {
      setResult(null)
      setReason('')
      setAck(false)
    }
  }

  const grantId = authority.grantId
  const ready = isSupervisor && !!grantId && reason.trim().length >= MIN_REASON && ack && !pending

  const submit = async () => {
    if (!grantId) return
    setPending(true)
    setError(null)
    try {
      setResult(await reissueGrant(grantId, identityId, reason.trim()))
      setNoGrant(null)
    } catch (e) {
      if (e instanceof ApiError && e.kind === 'not_implemented') {
        setError('This backend can’t issue a new grant yet. For the demo, run scripts/reset_demo.sh and reload.')
      } else {
        setError(e instanceof ApiError ? e.message : 'Could not issue a new grant.')
      }
    } finally {
      setPending(false)
    }
  }

  return (
    <Drawer open={open} title="Restore the agent’s authority" onClose={close}>
      {result ? (
        <div className="space-y-4">
          <p className="flex items-center gap-2 text-base font-semibold text-ok"><CheckCircle2 size={18} aria-hidden /> New grant issued</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-muted">New grant</dt><dd><Mono className="font-semibold">{result.grant_id}</Mono></dd>
            <dt className="text-muted">Replaces</dt><dd><Mono>{result.supersedes}</Mono> <span className="text-muted">(stays revoked)</span></dd>
            <dt className="text-muted">Expires</dt><dd>{formatDateTime(result.expires_at)}</dd>
            <dt className="text-muted">Issued by</dt><dd>{me.name}</dd>
          </dl>
          <p className="text-sm text-muted">
            Transfers that were halted stay stopped. The agent only acts for the person who holds the grant, so new requests have to be sent as {holder?.name ?? 'that person'}.
          </p>
          <div className="flex gap-2">
            {holder && holder._id !== identityId && (
              <button
                onClick={() => {
                  setIdentity(holder._id)
                  close()
                }}
                className="h-11 flex-1 rounded-xl bg-ok text-[15px] font-semibold text-bg hover:brightness-110"
              >
                Switch to {holder.name}
              </button>
            )}
            <button onClick={close} className={`h-11 rounded-xl text-[15px] font-semibold ${holder && holder._id !== identityId ? 'px-4 text-muted ring-1 ring-line hover:text-fg' : 'w-full bg-ok text-bg hover:brightness-110'}`}>
              {holder && holder._id !== identityId ? 'Stay as ' + me.name : 'Done'}
            </button>
          </div>
        </div>
      ) : (
        <form
          className="space-y-5"
          onSubmit={(e) => {
            e.preventDefault()
            if (ready) void submit()
          }}
        >
          <section className="rounded-xl bg-panel p-4 text-sm">
            <h3 className="flex items-center gap-2 font-semibold"><ShieldCheck size={16} className="text-info" aria-hidden /> What this does</h3>
            <ul className="mt-2 list-disc space-y-1.5 pl-5 text-muted">
              <li>Issues a <b className="text-fg">new</b> grant{grantId && <> to replace <Mono className="text-fg">{grantId}</Mono></>}. The revoked grant stays revoked and on record.</li>
              <li>Same permissions as before, never wider. The server sets a short expiry.</li>
              <li>Recorded in the ledger under your name, with your reason.</li>
              <li>Halted transfers do <b className="text-fg">not</b> resume. You send new requests.</li>
            </ul>
          </section>

          {!isSupervisor && (
            <p role="alert" className="rounded-lg bg-warn/10 px-3 py-2.5 text-sm text-warn ring-1 ring-warn/30">
              Only a supervisor can restore authority, so the agent can’t be re-enabled by the person it works for.
              The backend enforces this too.
              <button type="button" onClick={() => setIdentity('user:mokafor')} className="mt-2 block rounded-lg bg-warn px-3 py-1.5 text-[13px] font-medium text-bg hover:brightness-110">
                Switch demo identity to M. Okafor (supervisor)
              </button>
            </p>
          )}
          {!grantId && <p className="text-sm text-warn">There is no revoked grant to replace.</p>}

          <label className="block text-sm">
            <span className="font-medium">Why is it safe to restore?</span>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={!isSupervisor || pending}
              rows={3}
              placeholder="e.g. Revoked by mistake during a rehearsal. Reviewed with the technician."
              className="mt-1.5 w-full rounded-lg bg-panel px-3 py-2.5 text-sm ring-1 ring-line placeholder:text-muted/60 focus:ring-info disabled:opacity-50"
            />
            <span className="mt-1 block text-xs text-muted">{reason.trim().length < MIN_REASON ? `At least ${MIN_REASON} characters. ` : ''}This is saved permanently in the audit ledger.</span>
          </label>

          <label className="flex items-start gap-2.5 text-sm">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} disabled={!isSupervisor || pending} className="mt-1 size-4 accent-[#59e1c1]" />
            <span>I understand this lets the agent act again, and that I’m accountable for it.</span>
          </label>

          {error && <p role="alert" className="rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad ring-1 ring-bad/30">{error}</p>}

          <div className="flex gap-2">
            <button type="submit" disabled={!ready} className="flex h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-ok text-[15px] font-semibold text-bg hover:brightness-110">
              {pending ? <><Loader2 size={16} className="animate-spin" /> Waiting for the backend…</> : 'Issue new grant'}
            </button>
            <button type="button" onClick={close} className="h-11 rounded-xl px-4 text-sm text-muted ring-1 ring-line hover:text-fg">Cancel</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

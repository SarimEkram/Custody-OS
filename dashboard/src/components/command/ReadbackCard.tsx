import { ArrowRight, CheckCircle2, CircleAlert, Loader2, Volume2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { ApiError, confirmTransfer } from '../../lib/api'
import { groupLedger } from '../../lib/actions'
import { toolLabel } from '../../lib/format'
import { pickActiveTransfer, STOPPED_STATES, useCustodyStore } from '../../store/custodyStore'
import { Mono } from '../ui/Badge'
import { Drawer } from '../ui/Drawer'

/**
 * Read-back card. Shown when the backend sends a `confirmation_request`, or the transfer is blocked with
 * `readback_pending`. Confirming only tells the backend the person accepted the read-back; irreversible
 * steps still go through the gate on the server.
 *
 * "Review" opens the details without hiding the card, so the person can always come back and confirm.
 * The card leaves only when the backend reports the read-back confirmed.
 */
export function ReadbackCard() {
  const transfer = useCustodyStore((s) => pickActiveTransfer(s))
  const readbacks = useCustodyStore((s) => s.readbacks)
  const locations = useCustodyStore((s) => s.locations)
  const ledger = useCustodyStore((s) => s.ledger)
  const scans = useCustodyStore((s) => s.scans)
  const intake = useCustodyStore((s) => s.lastIntake)
  const identityId = useCustodyStore((s) => s.identityId)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reviewing, setReviewing] = useState(false)

  const done = useMemo(
    () => (transfer ? groupLedger(ledger.filter((e) => e.transfer_id === transfer._id), scans).filter((i) => i.kind === 'action' && i.status === 'Done') : []),
    [ledger, scans, transfer],
  )

  // If the backend never answers, say so and let the person retry instead of spinning forever.
  useEffect(() => {
    if (!pending) return
    const t = setTimeout(() => {
      setPending(false)
      setError('The backend has not responded yet. Check the Activity list: the transfer may have been stopped. You can try again.')
    }, 8000)
    return () => clearTimeout(t)
  }, [pending])

  // A halted or finished transfer can't be confirmed (e.g. the grant was revoked while this card was open).
  const stopped = !!transfer && (transfer.halt === true || STOPPED_STATES.includes(transfer.state))
  const waiting = transfer && transfer.blocked_reason === 'readback_pending' && !transfer.readback_confirmed
  const rb = transfer ? readbacks[transfer._id] : undefined
  if (transfer && stopped) {
    // Stopped while we were still waiting for the person: say so, instead of the card silently vanishing.
    const mine = ledger.filter((e) => e.transfer_id === transfer._id)
    const neverConfirmed = mine.some((e) => e.decision === 'pending_confirmation') && !mine.some((e) => e.entry_type === 'confirmation')
    if (!neverConfirmed) return null
    return (
      <section role="status" className="slot-in rounded-xl bg-surface p-4 ring-1 ring-bad/40">
        <h3 className="flex items-center gap-2 text-sm font-semibold"><CircleAlert size={15} className="text-bad" aria-hidden /> This transfer was stopped before you confirmed it</h3>
        <p className="mt-1 text-[13px] text-muted">
          {scans.some((s) => s.sample_id === transfer.sample_id && s.type === 'checkout')
            ? 'The sample had already left storage, so it is being recovered.'
            : 'Nothing was checked out: the sample never left storage.'}{' '}
          This request can no longer be confirmed. Once the agent has an active grant, send a new request.
        </p>
      </section>
    )
  }
  if (!transfer || (!waiting && !rb)) return null

  const name = (id: string) => locations.find((l) => l._id === id)?.name ?? id
  const from = name(rb?.from ?? transfer.from)
  const to = name(rb?.to ?? transfer.to)
  const sampleId = rb?.sample_id ?? transfer.sample_id
  const request = intake && intake.request_id === transfer.request_id ? intake.transcript : null

  const confirm = async () => {
    setPending(true)
    setError(null)
    try {
      await confirmTransfer(transfer._id, identityId, 'button')
      setReviewing(false)
      // Stay in "waiting" until the backend's transfer event clears this card.
    } catch (e) {
      setPending(false)
      setError(e instanceof ApiError ? e.message : 'Confirmation failed.')
    }
  }

  const confirmButton = (
    <button onClick={() => void confirm()} disabled={pending} className="flex h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-ok text-[15px] font-semibold text-bg hover:brightness-110">
      {pending ? <><Loader2 size={16} className="animate-spin" /> Waiting for the backend…</> : 'Confirm transfer'}
    </button>
  )

  return (
    <section role="region" aria-label="Confirm transfer" className="slot-in needs-you rounded-xl bg-surface p-5 ring-1 ring-warn/60">
      <h3 className="text-base font-semibold">Confirm this transfer</h3>
      <div className="mt-3 flex items-center gap-3 text-[15px]">
        <Mono className="font-semibold">{sampleId}</Mono>
        <span className="flex items-center gap-2 text-muted">
          {from} <ArrowRight size={14} aria-hidden /> <span className="text-fg">{to}</span>
        </span>
      </div>
      {(rb?.requested_arrival || rb?.estimated_arrival) && (
        <p className="mt-1 text-[13px] text-muted">
          {rb.requested_arrival && <>Requested for {rb.requested_arrival}</>}
          {rb.requested_arrival && rb.estimated_arrival && ' · '}
          {rb.estimated_arrival && <>arrives about {rb.estimated_arrival}</>}
        </p>
      )}
      {rb?.readback_text && <p className="mt-3 rounded-lg bg-panel px-3 py-2.5 text-[14px] leading-snug">“{rb.readback_text}”</p>}
      {rb?.audio_url && (
        <button onClick={() => void new Audio(rb.audio_url ?? '').play().catch(() => setError('Could not play the audio. The text above is what counts.'))} className="mt-2 flex items-center gap-1.5 text-xs text-info hover:underline">
          <Volume2 size={14} aria-hidden /> Play read-back
        </button>
      )}
      <p className="mt-3 text-xs text-muted">Nothing irreversible happens until you confirm.</p>
      {error && <p role="alert" className="mt-2 text-sm text-bad">{error}</p>}
      <div className="mt-3 flex gap-2">
        {confirmButton}
        <button onClick={() => setReviewing(true)} disabled={pending} className="h-11 rounded-xl px-4 text-sm text-muted ring-1 ring-line hover:text-fg">
          Review
        </button>
      </div>

      <Drawer open={reviewing} title="Review before confirming" onClose={() => setReviewing(false)}>
        <div className="space-y-6">
          <section>
            <h4 className="text-sm font-medium text-muted">Your request</h4>
            {request && <p className="mt-1 text-[15px]">“{request}”</p>}
            <p className="mt-2 flex items-center gap-2 text-[15px]">
              <Mono className="font-semibold">{sampleId}</Mono>
              <span className="text-muted">{from}</span> <ArrowRight size={14} className="text-muted" aria-hidden /> <span>{to}</span>
            </p>
            <p className="mt-1 text-xs text-muted">Requested by {transfer.requested_by} under {transfer.grant_id}.</p>
          </section>

          <section>
            <h4 className="flex items-center gap-2 text-sm font-medium text-muted"><CheckCircle2 size={15} className="text-ok" aria-hidden /> Already prepared (can be undone)</h4>
            <ul className="mt-2 space-y-1.5 text-[14px]">
              {done.length === 0 && <li className="text-muted">Nothing yet.</li>}
              {done.map((i) => (
                <li key={i.id} className="flex gap-2"><CheckCircle2 size={14} className="mt-1 shrink-0 text-ok" aria-hidden /><span><span className="font-medium">{toolLabel(i.entry.tool)}</span> <span className="text-muted">{i.detail}</span></span></li>
              ))}
            </ul>
          </section>

          <section>
            <h4 className="flex items-center gap-2 text-sm font-medium text-muted"><CircleAlert size={15} className="text-warn" aria-hidden /> Happens after you confirm (cannot be undone)</h4>
            <ol className="mt-2 space-y-1.5 text-[14px]">
              <li>Check out <Mono>{sampleId}</Mono> from {from}</li>
              <li>Hand it to the courier</li>
              <li>Deliver it to {to}</li>
            </ol>
            <p className="mt-2 text-xs text-muted">Each step is checked by the policy gate first and proven by a scanner. If you need to abort later, use Stop agent: reservations are rolled back and the sample is returned.</p>
          </section>

          {error && <p role="alert" className="text-sm text-bad">{error}</p>}
          <div className="flex gap-2">
            {confirmButton}
            <button onClick={() => setReviewing(false)} className="h-11 rounded-xl px-4 text-sm text-muted ring-1 ring-line hover:text-fg">Back</button>
          </div>
        </div>
      </Drawer>
    </section>
  )
}

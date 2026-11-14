import { CheckCircle2, Hourglass, RotateCcw, Undo2 } from 'lucide-react'
import { denialFor } from '../../lib/authority'
import { pickActiveTransfer, useCustodyStore } from '../../store/custodyStore'

/**
 * Rollback vs recovery, side by side. Only appears once a transfer was stopped:
 * reservations can be undone, a sample already out of storage must be brought back and re-verified.
 */
export function WorkflowStatus() {
  const t = useCustodyStore((s) => pickActiveTransfer(s))
  const ledger = useCustodyStore((s) => s.ledger)
  const scans = useCustodyStore((s) => s.scans)

  if (!t || !['revoked', 'recovering', 'recovered', 'cancelled'].includes(t.state)) return null
  // Denied outright by the gate: nothing ran, so there is nothing to roll back (the denial card explains it).
  if (denialFor(ledger, scans, t._id) && !ledger.some((e) => e.transfer_id === t._id && e.entry_type === 'compensation')) return null

  const mine = ledger.filter((e) => e.transfer_id === t._id)
  const compensations = mine.filter((e) => e.entry_type === 'compensation')
  const recovery = mine.filter((e) => e.entry_type === 'recovery')
  const exposure = [...mine].reverse().find((e) => e.entry_type === 'exposure')
  const checkedIn = scans.some((s) => s.sample_id === t.sample_id && s.type === 'checkin')
  // Recovery only applies once the sample actually left storage (proven by a scan). Before that, rollback is all there is.
  const leftStorage = scans.some((s) => s.sample_id === t.sample_id && (s.type === 'checkout' || s.type === 'handoff'))
  const showRecovery = t.state === 'recovering' || t.state === 'recovered' || (t.state === 'revoked' && leftStorage)

  return (
    <section className="slot-in rounded-xl bg-surface p-4 ring-1 ring-line/60" aria-label="Rollback and recovery">
      <div className={`grid gap-4 ${showRecovery ? 'sm:grid-cols-2' : ''}`}>
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold"><Undo2 size={15} className="text-ok" aria-hidden /> Rolled back</h3>
          <p className="text-xs text-muted">Reversible steps can simply be cancelled.</p>
          <ul className="mt-2 space-y-1.5 text-[13px]">
            {compensations.length === 0 && <li className="text-muted">Nothing yet</li>}
            {compensations.map((e) => (
              <li key={e._id} className="flex gap-2"><CheckCircle2 size={14} className="mt-0.5 shrink-0 text-ok" aria-hidden />{e.summary ?? e.tool}</li>
            ))}
          </ul>
        </div>
        {showRecovery && (
          <div>
            <h3 className="flex items-center gap-2 text-sm font-semibold"><RotateCcw size={15} className="text-warn" aria-hidden /> Recovered</h3>
            <p className="text-xs text-muted">A sample already out can’t be undone. It goes back, and a scan proves it.</p>
            <ul className="mt-2 space-y-1.5 text-[13px]">
              {recovery.length === 0 && <li className="text-muted">Not started</li>}
              {recovery.map((e) => (
                <li key={e._id} className="flex gap-2"><RotateCcw size={14} className="mt-0.5 shrink-0 text-warn" aria-hidden />{e.summary ?? e.tool}</li>
              ))}
              <li className="flex gap-2">
                {checkedIn ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-ok" aria-hidden /> : <Hourglass size={14} className="mt-0.5 shrink-0 text-warn" aria-hidden />}
                <span className={checkedIn ? 'text-ok' : 'text-warn'}>{checkedIn ? 'Scanner confirmed check-in' : 'Waiting for scanner check-in'}</span>
              </li>
              {exposure && (
                <li className="pt-1">
                  {String(exposure.response?.minutes_out ?? '?')} min out,{' '}
                  <span className={exposure.response?.exceeded ? 'font-semibold text-bad' : 'font-semibold text-ok'}>{exposure.response?.exceeded ? 'over the limit' : 'within the limit'}</span>
                </li>
              )}
            </ul>
          </div>
        )}
      </div>
    </section>
  )
}

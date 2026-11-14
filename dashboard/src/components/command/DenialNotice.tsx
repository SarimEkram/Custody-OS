import { ShieldX } from 'lucide-react'
import { useMemo } from 'react'
import { denialFor } from '../../lib/authority'
import { explainRule, GRANT_RULES } from '../../lib/rules'
import { pickActiveTransfer, useCustodyStore } from '../../store/custodyStore'
import { Mono } from '../ui/Badge'

/** Why the gate refused a request, in plain language, with what to do next. Never hides the real rule. */
export function DenialNotice() {
  const transfer = useCustodyStore((s) => pickActiveTransfer(s))
  const ledger = useCustodyStore((s) => s.ledger)
  const scans = useCustodyStore((s) => s.scans)
  const selectEntry = useCustodyStore((s) => s.selectEntry)
  const setRestoreOpen = useCustodyStore((s) => s.setRestoreOpen)

  const denial = useMemo(() => denialFor(ledger, scans, transfer?._id), [ledger, scans, transfer])
  if (!transfer || !denial) return null

  const primary = explainRule(denial.rules[0] ?? 'denied')
  return (
    <section role="alert" className="slot-in rounded-xl bg-surface p-4 ring-1 ring-bad/40">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><ShieldX size={16} className="text-bad" aria-hidden /> Request denied: {primary.title}</h3>
      <p className="mt-1.5 text-[13px]">{primary.why}</p>
      <p className="mt-1 text-[13px] text-muted">
        {denial.nothingPhysical ? 'Nothing physical happened and no lab system was changed. ' : ''}
        The attempt is recorded in the ledger.
      </p>
      <p className="mt-2 text-[13px]"><span className="font-medium">What to do:</span> <span className="text-muted">{primary.next}</span></p>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 text-xs text-muted">
        {denial.rules.map((r) => <Mono key={r}>{r}</Mono>)}
        <button onClick={() => selectEntry(denial.entry._id)} className="text-info hover:underline">See the decision</button>
        {denial.rules.some((r) => GRANT_RULES.includes(r)) && (
          <button onClick={() => setRestoreOpen(true)} className="ml-auto rounded-lg bg-warn px-3 py-1.5 text-[13px] font-medium text-bg hover:brightness-110">Restore authority</button>
        )}
      </div>
    </section>
  )
}

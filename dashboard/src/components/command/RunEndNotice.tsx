import { TriangleAlert } from 'lucide-react'
import { useMemo } from 'react'
import { explainRunEnd, runEndFor, runEndStatus } from '../../lib/runEnd'
import { pickActiveTransfer, useCustodyStore } from '../../store/custodyStore'
import { Mono } from '../ui/Badge'

/**
 * The agent's run ended abnormally: it hit a limit, the AI model failed, or something broke.
 * Deliberately different from a denial: nothing was approved or refused by policy, the agent itself failed.
 */
export function RunEndNotice() {
  const transfer = useCustodyStore((s) => pickActiveTransfer(s))
  const ledger = useCustodyStore((s) => s.ledger)
  const selectEntry = useCustodyStore((s) => s.selectEntry)

  const entry = useMemo(() => runEndFor(ledger, transfer?._id), [ledger, transfer])
  if (!transfer || !entry) return null

  const status = runEndStatus(entry)
  const why = explainRunEnd(status)
  return (
    <section role="alert" className="slot-in rounded-xl bg-surface p-4 ring-1 ring-warn/50">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><TriangleAlert size={16} className="text-warn" aria-hidden /> The agent stopped early: {why.title}</h3>
      <p className="mt-1.5 text-[13px]">{why.why}</p>
      <p className="mt-1 text-[13px] text-muted">
        <span className="font-medium text-fg">This was a failure, not a policy decision.</span> Nothing was approved or refused by the gate.
        {entry.reason ? <> Details: {entry.reason}</> : null}
      </p>
      <p className="mt-2 text-[13px]"><span className="font-medium">What happens next:</span> <span className="text-muted">{why.next}</span></p>
      <div className="mt-2 flex items-center gap-3 text-xs text-muted">
        <Mono>{status}</Mono>
        <button onClick={() => selectEntry(entry._id)} className="text-info hover:underline">See the ledger entry</button>
      </div>
    </section>
  )
}

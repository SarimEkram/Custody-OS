import { Check, Inbox, Loader2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ApiError, decideApproval } from '../../lib/api'
import { canDecideApproval, formatDateTime, toolLabel } from '../../lib/format'
import { IDENTITIES } from '../../lib/seed'
import type { Approval } from '../../lib/types'
import { useCustodyStore } from '../../store/custodyStore'
import { Badge, Mono } from '../ui/Badge'
import { Panel } from '../ui/Panel'

function ApprovalCard({ approval }: { approval: Approval }) {
  const identityId = useCustodyStore((s) => s.identityId)
  const requester = useCustodyStore((s) => s.transfers[approval.transfer_id]?.requested_by)
  const me = IDENTITIES.find((i) => i._id === identityId) ?? IDENTITIES[0]
  const [submitting, setSubmitting] = useState<'approved' | 'denied' | null>(null)
  const [error, setError] = useState<string | null>(null)

  // The pending controls disappear when the backend reports a decision over SSE.
  useEffect(() => {
    if (!submitting) return
    const t = setTimeout(() => {
      setSubmitting(null)
      setError('The backend has not recorded the decision yet. Check the Activity list, then try again.')
    }, 8000)
    return () => clearTimeout(t)
  }, [submitting])

  const allowed = canDecideApproval(me, approval)
  const decide = async (decision: 'approved' | 'denied') => {
    setSubmitting(decision)
    setError(null)
    try {
      await decideApproval(approval._id, identityId, decision)
      // Do NOT mark it approved here. Wait for the backend's approval event.
    } catch (e) {
      setSubmitting(null)
      setError(e instanceof ApiError ? e.message : 'Decision failed.')
    }
  }

  const tone = approval.status === 'approved' ? 'ok' : approval.status === 'denied' ? 'bad' : 'warn'

  return (
    <li className="rounded-lg bg-panel p-3">
      <div className="flex items-center justify-between gap-2">
        <Mono className="font-semibold">{approval._id}</Mono>
        <Badge tone={tone} label={approval.status.toUpperCase()} />
      </div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
        <dt className="text-muted">Sample</dt><dd><Mono>{approval.sample_id}</Mono></dd>
        <dt className="text-muted">Action</dt><dd>{toolLabel(approval.requested_for_tool)}</dd>
        <dt className="text-muted">Why</dt><dd><Mono>{approval.reason_requested}</Mono></dd>
        <dt className="text-muted">Required role</dt><dd>{approval.approver_role ?? 'supervisor'}</dd>
        <dt className="text-muted">Requested by</dt><dd>{approval.requested_by ?? requester ?? 'unknown'}</dd>
        {approval.expires_at && (<><dt className="text-muted">Expires</dt><dd>{formatDateTime(approval.expires_at)}</dd></>)}
        {approval.decided_by && (<><dt className="text-muted">Decided by</dt><dd>{approval.decided_by}</dd></>)}
      </dl>
      {approval.status === 'pending' && (
        <>
          <div className="mt-3 flex gap-2">
            <button
              onClick={() => void decide('approved')}
              disabled={!allowed || submitting !== null}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-ok px-3 py-2 text-sm font-bold text-bg hover:brightness-110"
            >
              {submitting === 'approved' ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} aria-hidden />} Approve
            </button>
            <button
              onClick={() => void decide('denied')}
              disabled={!allowed || submitting !== null}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-bad/60 px-3 py-2 text-sm font-bold text-bad hover:bg-bad/10"
            >
              {submitting === 'denied' ? <Loader2 size={15} className="animate-spin" /> : <X size={15} aria-hidden />} Deny
            </button>
          </div>
          {!allowed && <p className="mt-2 text-xs text-warn">Only a supervisor can decide. Switch the demo identity to M. Okafor.</p>}
          {submitting && <p className="mt-2 text-xs text-muted">Submitted. Waiting for the backend to record the decision…</p>}
          {error && <p role="alert" className="mt-2 text-sm text-bad">{error}</p>}
        </>
      )}
    </li>
  )
}

export function ApprovalInbox() {
  const approvals = useCustodyStore((s) => s.approvals)
  const list = Object.values(approvals).sort((a, b) => Number(b.status === 'pending') - Number(a.status === 'pending'))
  if (list.length === 0) return null
  const pending = list.filter((a) => a.status === 'pending').length
  return (
    <Panel
      title="Needs a supervisor"
      icon={<Inbox size={14} aria-hidden />}
      className={`slot-in ${pending > 0 ? 'ring-warn/50 needs-you' : ''}`}
      actions={pending > 0 ? <Badge tone="warn" label={`${pending} waiting`} /> : undefined}
    >
      <ul className="space-y-2">{list.map((a) => <ApprovalCard key={a._id} approval={a} />)}</ul>
    </Panel>
  )
}

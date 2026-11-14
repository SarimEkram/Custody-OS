import { ApprovalInbox } from '../components/approvals/ApprovalInbox'
import { AuthorityBanner } from '../components/command/AuthorityBanner'
import { CommandBar } from '../components/command/CommandBar'
import { DenialNotice } from '../components/command/DenialNotice'
import { ReadbackCard } from '../components/command/ReadbackCard'
import { RunEndNotice } from '../components/command/RunEndNotice'
import { LedgerStream } from '../components/ledger/LedgerStream'
import { LabMap } from '../components/map/LabMap'
import { WorkflowStatus } from '../components/workflow/WorkflowStatus'

/**
 * Desktop: the map is the stage (60%). The right column holds only what needs a person
 * (read-back, approval, recovery) above the activity ledger. Narrow screens stack, map first.
 */
export function ControlCenter() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <CommandBar />
      <AuthorityBanner />
      <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto p-4 lg:grid-cols-5 lg:overflow-hidden">
        <div className="min-h-[560px] lg:col-span-3 lg:min-h-0">
          <LabMap />
        </div>
        <div className="flex min-h-0 flex-col gap-4 lg:col-span-2">
          <DenialNotice />
          <RunEndNotice />
          <ReadbackCard />
          <ApprovalInbox />
          <WorkflowStatus />
          <div className="min-h-[300px] flex-1">
            <LedgerStream />
          </div>
        </div>
      </div>
    </div>
  )
}

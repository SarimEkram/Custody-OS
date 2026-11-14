import { Check } from 'lucide-react'
import { stateLabel } from '../../lib/format'
import type { TransferState } from '../../lib/types'
import { pickActiveTransfer, useCustodyStore } from '../../store/custodyStore'
import { ExposureClock } from './ExposureCountdown'
import { GrantChip, StopAgentButton, StopStatusLine } from './StopAgentButton'

const STEPS: { state: TransferState; label: string }[] = [
  { state: 'requested', label: 'Requested' },
  { state: 'reserved', label: 'Reserved' },
  { state: 'checked_out', label: 'Checked out' },
  { state: 'in_transit', label: 'In transit' },
  { state: 'received', label: 'Received' },
]

/** How far the transfer has verifiably got. For halted transfers, derive it from scanner evidence. */
function useReached(): { index: number; broken: boolean } {
  const t = useCustodyStore((s) => pickActiveTransfer(s))
  const scans = useCustodyStore((s) => s.scans)
  if (!t) return { index: -1, broken: false }
  const direct = STEPS.findIndex((s) => s.state === t.state)
  if (direct >= 0) return { index: direct, broken: false }
  if (t.state === 'blocked') return { index: 1, broken: false }
  const mine = scans.filter((s) => s.sample_id === t.sample_id)
  const index = mine.some((s) => s.type === 'handoff') ? 3 : mine.some((s) => s.type === 'checkout') ? 2 : 1
  return { index, broken: true }
}

const END_TONE: Partial<Record<TransferState, { ring: string; dot: string; line: string }>> = {
  revoked: { ring: 'ring-2 ring-bad', dot: 'bg-bad', line: 'bg-ok' },
  recovering: { ring: 'ring-2 ring-warn', dot: 'bg-warn', line: 'bg-ok' },
  recovered: { ring: 'ring-2 ring-ok', dot: 'bg-ok', line: 'bg-ok' },
  cancelled: { ring: 'ring-2 ring-muted', dot: 'bg-muted', line: 'bg-ok' },
}

/** One docked control strip: how far the transfer got, then the clock, authority and Stop. */
export function MissionBar() {
  const t = useCustodyStore((s) => pickActiveTransfer(s))
  const { index, broken } = useReached()
  const fill = index < 0 ? 0 : (index / (STEPS.length - 1)) * 100
  const end = t ? END_TONE[t.state] : undefined

  return (
    <div className="rounded-2xl bg-panel/70 px-5 py-4 ring-1 ring-line/70 backdrop-blur-sm">
      <StopStatusLine />
      <ol className="relative mt-1 grid grid-cols-5" aria-label={t ? `Transfer progress: ${stateLabel(t.state)}` : 'Transfer progress'}>
        <span className="absolute top-[9px] right-[10%] left-[10%] h-0.5 rounded bg-line" aria-hidden />
        <span className="absolute top-[9px] left-[10%] h-0.5 rounded bg-ok transition-[width] duration-700 ease-out" style={{ width: `${fill * 0.8}%` }} aria-hidden />
        {STEPS.map((s, i) => {
          const done = i < index || (i === index && s.state === 'received')
          const current = i === index && !done
          const ring = current ? (broken && end ? end.ring : 'ring-2 ring-info') : ''
          const dot = broken && end ? end.dot : 'bg-info'
          return (
            <li key={s.state} className="relative flex flex-col items-center gap-1.5" aria-current={current ? 'step' : undefined}>
              <span className={`flex size-5 items-center justify-center rounded-full transition-colors duration-500 ${done ? 'bg-ok text-bg' : current ? `bg-panel ${ring}` : 'bg-panel ring-1 ring-line'}`}>
                {done ? <Check size={12} strokeWidth={3} aria-hidden /> : current ? <span className={`size-1.5 rounded-full ${dot}`} /> : null}
              </span>
              <span className={`text-xs ${done || current ? 'font-medium text-fg' : 'text-muted'}`}>{s.label}</span>
            </li>
          )
        })}
      </ol>
      <div className="mt-4 flex items-center gap-10 border-t border-line/50 pt-4">
        <ExposureClock />
        <GrantChip />
        <div className="ml-auto">
          <StopAgentButton />
        </div>
      </div>
    </div>
  )
}

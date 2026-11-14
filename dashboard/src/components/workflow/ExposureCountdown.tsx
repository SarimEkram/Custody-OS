import { Timer } from 'lucide-react'
import { useNow } from '../../hooks/useNow'
import { elapsedSeconds, exposureStatus, formatClock, type ExposureLevel } from '../../lib/format'
import type { LedgerEntry, Sample } from '../../lib/types'
import { activeExposure, pickActiveTransfer, useCustodyStore } from '../../store/custodyStore'
import { Mono } from '../ui/Badge'

export interface ExposureView {
  sample: Sample | undefined
  /** True only once the scanner has verified checkout. "Sent" never starts the clock. */
  started: boolean
  /** Seconds out of storage so far (counts up). */
  elapsed: number
  remaining: number
  overBy: number
  percent: number
  level: ExposureLevel
  reported: LedgerEntry | undefined
}

export function useExposure(): ExposureView {
  const t = useCustodyStore((s) => pickActiveTransfer(s))
  const scans = useCustodyStore((s) => s.scans)
  const samples = useCustodyStore((s) => s.samples)
  const ledger = useCustodyStore((s) => s.ledger)
  const now = useNow(1000)

  const sampleId = t?.sample_id
  const sample = sampleId ? samples[sampleId] : undefined
  const exposure = sampleId ? activeExposure(sampleId, scans) : null
  const reported = sampleId ? [...ledger].reverse().find((e) => e.sample_id === sampleId && e.entry_type === 'exposure') : undefined
  const elapsed = exposure ? elapsedSeconds(exposure.checkedOutAt, now) : 0
  const status = exposureStatus(elapsed, sample?.max_minutes_out ?? 0)
  return { sample, started: !!exposure, elapsed, remaining: status.remaining, overBy: status.overBy, percent: status.percent, level: sample ? status.level : 'safe', reported }
}

const LEVEL_TEXT: Record<ExposureLevel, string> = { safe: 'text-ok', approaching: 'text-warn', exceeded: 'text-bad' }
const LEVEL_BAR: Record<ExposureLevel, string> = { safe: 'bg-ok', approaching: 'bg-warn', exceeded: 'bg-bad' }

const spoken = (secs: number) => `${Math.floor(secs / 60)} minutes ${secs % 60} seconds`

/**
 * How long the sample has been out of storage, counting up from the scanner-verified checkout.
 * The limit is a fixed reference (a bar fills toward it). Past the limit the clock keeps counting and says by how much.
 * Keeps running through recovery until the scanner confirms check-in.
 */
export function ExposureClock() {
  const { sample, started, elapsed, remaining, overBy, percent, level, reported } = useExposure()
  const demoSpeed = useCustodyStore((s) => s.demoSpeed)

  let body
  if (!sample) {
    body = <p className="text-[13px] text-muted">No sample selected</p>
  } else if (started) {
    body = (
      <>
        <div className="flex items-baseline gap-2">
          <span
            role="timer"
            aria-label={`Out of the freezer for ${spoken(elapsed)}. Limit ${sample.max_minutes_out} minutes.`}
            className={`font-mono text-[32px] leading-none font-semibold tabular-nums ${LEVEL_TEXT[level]}`}
          >
            {formatClock(elapsed)}
          </span>
          <span className="text-xs text-muted">of {sample.max_minutes_out} min max</span>
        </div>
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-line/70" aria-hidden>
          <div className={`h-full rounded-full transition-[width] duration-1000 ease-linear ${LEVEL_BAR[level]}`} style={{ width: `${percent}%` }} />
        </div>
        <p className={`mt-1.5 text-xs ${LEVEL_TEXT[level]}`}>
          {level === 'exceeded' ? `Over the limit by ${formatClock(overBy)}` : level === 'approaching' ? `Nearing the limit: ${formatClock(remaining)} left` : 'Within the limit'}
        </p>
      </>
    )
  } else if (reported) {
    body = (
      <>
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-2xl leading-none font-semibold tabular-nums">{String(reported.response?.minutes_out ?? '?')} min</span>
          <span className="text-xs text-muted">of {sample.max_minutes_out} min max</span>
        </div>
        <p className="mt-1.5 text-xs">
          <span className={reported.response?.exceeded ? 'text-bad' : 'text-ok'}>{reported.response?.exceeded ? 'Over the limit' : 'Within the limit'}</span>
          <span className="text-muted"> · the scanner stopped the clock</span>
        </p>
      </>
    )
  } else {
    body = (
      <>
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[32px] leading-none font-semibold text-line">0:00</span>
          <span className="text-xs text-muted">of {sample.max_minutes_out} min max</span>
        </div>
        <p className="mt-2 text-xs text-muted" title="The clock starts when the scanner verifies checkout, not when the agent asks for it.">Starts at scanned checkout</p>
      </>
    )
  }

  return (
    <div className="min-w-[190px]">
      <p className="mb-1.5 flex items-center gap-1.5 text-xs text-muted">
        <Timer size={13} aria-hidden /> Time out of freezer {sample && <Mono className="text-fg">{sample._id}</Mono>}
      </p>
      {body}
      {demoSpeed && demoSpeed !== 1 && <p className="mt-1 text-[11px] text-muted">Demo time ×{demoSpeed}</p>}
    </div>
  )
}

import { Timer } from 'lucide-react'
import { useNow } from '../../hooks/useNow'
import { exposureLevel, formatClock, remainingSeconds, type ExposureLevel } from '../../lib/format'
import type { LedgerEntry, Sample } from '../../lib/types'
import { activeExposure, pickActiveTransfer, useCustodyStore } from '../../store/custodyStore'
import { Mono } from '../ui/Badge'

export interface ExposureView {
  sample: Sample | undefined
  /** True only once the scanner has verified checkout. "Sent" never starts the clock. */
  started: boolean
  remaining: number
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
  const remaining = sample && exposure ? remainingSeconds(exposure.checkedOutAt, sample.max_minutes_out, now) : 0
  return { sample, started: !!exposure, remaining, level: sample ? exposureLevel(remaining, sample.max_minutes_out) : 'safe', reported }
}

const LEVEL_TEXT: Record<ExposureLevel, string> = { safe: 'text-ok', approaching: 'text-warn', exceeded: 'text-bad' }
const LEVEL_NOTE: Record<ExposureLevel, string> = { safe: 'Within limit', approaching: 'Nearing limit', exceeded: 'Limit exceeded' }

/** Out-of-freezer clock. Keeps running through recovery until the scanner confirms check-in. */
export function ExposureClock() {
  const { sample, started, remaining, level, reported } = useExposure()
  const demoSpeed = useCustodyStore((s) => s.demoSpeed)

  let body
  if (!sample) {
    body = <p className="text-[13px] text-muted">No sample selected</p>
  } else if (started) {
    body = (
      <>
        <div className={`font-mono text-[32px] leading-none font-semibold tabular-nums ${LEVEL_TEXT[level]}`}>{formatClock(remaining)}</div>
        <p className="mt-1 text-xs text-muted">
          <span className={LEVEL_TEXT[level]}>{LEVEL_NOTE[level]}</span> · {sample.max_minutes_out} min max
        </p>
      </>
    )
  } else if (reported) {
    body = (
      <>
        <div className="font-mono text-2xl leading-none font-semibold tabular-nums">{String(reported.response?.minutes_out ?? '?')} min</div>
        <p className="mt-1 text-xs">
          <span className={reported.response?.exceeded ? 'text-bad' : 'text-ok'}>{reported.response?.exceeded ? 'Limit exceeded' : 'Within limit'}</span>
          <span className="text-muted"> · scanner stopped the clock</span>
        </p>
      </>
    )
  } else {
    body = (
      <>
        <div className="font-mono text-[32px] leading-none font-semibold text-line">–:––</div>
        <p className="mt-1 text-xs text-muted" title="The clock starts when the scanner verifies checkout, not when the agent asks for it.">Starts at scanned checkout</p>
      </>
    )
  }

  return (
    <div className="min-w-[150px]">
      <p className="mb-1.5 flex items-center gap-1.5 text-xs text-muted">
        <Timer size={13} aria-hidden /> Time out of freezer {sample && <Mono className="text-fg">{sample._id}</Mono>}
      </p>
      {body}
      {demoSpeed && demoSpeed !== 1 && <p className="mt-1 text-[11px] text-muted">Demo time ×{demoSpeed}</p>}
    </div>
  )
}

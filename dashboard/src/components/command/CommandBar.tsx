import { AlertTriangle, ArrowRight, Loader2, Mic, Send } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ApiError, sendTypedRequest, sendVoiceRequest } from '../../lib/api'
import { useMediaRecorder } from '../../hooks/useMediaRecorder'
import type { IntakeResponse } from '../../lib/types'
import { useAuthority } from '../../hooks/useAuthority'
import { denialFor } from '../../lib/authority'
import { useCustodyStore } from '../../store/custodyStore'
import { Badge, Mono } from '../ui/Badge'

type Phase = 'idle' | 'recording' | 'uploading' | 'ready' | 'clarify' | 'error'

const needsClarification = (r: IntakeResponse): boolean =>
  r.low_confidence_terms.length > 0 || !r.transfer_id || !r.parsed.sample_id || !r.parsed.from || !r.parsed.to || (r.alternatives?.length ?? 0) > 0

/** Highlights backend-reported low-confidence terms inside the transcript. */
function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  if (terms.length === 0) return <>{text}</>
  const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const parts = text.split(new RegExp(`(${escaped.join('|')})`, 'gi'))
  return (
    <>
      {parts.map((p, i) =>
        terms.some((t) => t.toLowerCase() === p.toLowerCase()) ? (
          <mark key={i} className="rounded bg-warn/25 px-0.5 text-warn underline decoration-dotted underline-offset-4">{p}</mark>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  )
}

export function CommandBar() {
  const identityId = useCustodyStore((s) => s.identityId)
  const setLastIntake = useCustodyStore((s) => s.setLastIntake)
  const setActiveTransfer = useCustodyStore((s) => s.setActiveTransfer)
  const intake = useCustodyStore((s) => s.lastIntake)
  const ledger = useCustodyStore((s) => s.ledger)
  const scans = useCustodyStore((s) => s.scans)
  const authority = useAuthority()
  const denied = !!denialFor(ledger, scans, intake?.transfer_id)

  const rec = useMediaRecorder()
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState<string | null>(null)
  const [slow, setSlow] = useState(false)
  const [text, setText] = useState('')
  const [draft, setDraft] = useState('')
  const typedRef = useRef<HTMLInputElement>(null)
  const holding = useRef(false)

  const accept = useCallback(
    (r: IntakeResponse) => {
      setLastIntake(r)
      setDraft(r.transcript)
      if (r.transfer_id) setActiveTransfer(r.transfer_id)
      if (r.error) {
        setPhase('error')
        setMessage(r.error)
      } else {
        setPhase(needsClarification(r) ? 'clarify' : 'ready')
        setMessage(null)
      }
    },
    [setActiveTransfer, setLastIntake],
  )

  const fail = useCallback((e: unknown, voice: boolean) => {
    const base = e instanceof ApiError ? e.message : 'Unexpected error.'
    setPhase('error')
    setMessage(voice ? `${base} You can type the request instead.` : base)
    if (voice) typedRef.current?.focus()
  }, [])

  // Nudge after 5 s of waiting on transcription (DEMO.md voice safety).
  useEffect(() => {
    if (phase !== 'uploading') return
    const t = setTimeout(() => setSlow(true), 5000)
    return () => {
      clearTimeout(t)
      setSlow(false)
    }
  }, [phase])

  const begin = async () => {
    if (holding.current || phase === 'uploading') return
    holding.current = true
    setMessage(null)
    await rec.start()
    setPhase('recording')
  }

  const finish = async () => {
    if (!holding.current) return
    holding.current = false
    const blob = await rec.stop()
    if (!blob) {
      setPhase('idle')
      setMessage('That was too short. Hold the button while you speak.')
      return
    }
    setPhase('uploading')
    try {
      accept(await sendVoiceRequest(blob, identityId))
    } catch (e) {
      fail(e, true)
    }
  }

  useEffect(() => {
    if (rec.error && rec.status !== 'recording') {
      holding.current = false
      setPhase('error')
      setMessage(rec.error)
      typedRef.current?.focus()
    }
  }, [rec.error, rec.status])

  const submitTyped = async (value: string, clear: boolean) => {
    const v = value.trim()
    if (!v) return
    setPhase('uploading')
    setMessage(null)
    try {
      accept(await sendTypedRequest(v, identityId))
      if (clear) setText('')
    } catch (e) {
      fail(e, false)
    }
  }

  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) {
      e.preventDefault()
      void begin()
    }
  }
  const onKeyUp = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === ' ' || e.key === 'Enter') void finish()
  }

  const recording = phase === 'recording' && rec.status === 'recording'
  const busy = phase === 'uploading'
  const unsupported = rec.status === 'unsupported'

  const applyAlternative = (field: 'sample_id' | 'from' | 'to', option: string) => {
    const current = intake?.parsed[field]
    setDraft((d) => (current && d.includes(current) ? d.replace(current, option) : `${d} ${option}`.trim()))
  }

  return (
    <div className="border-b border-line/60 bg-surface/40 px-5 py-3">
      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={busy || unsupported}
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId)
            void begin()
          }}
          onPointerUp={() => void finish()}
          onPointerCancel={() => void finish()}
          onKeyDown={onKey}
          onKeyUp={onKeyUp}
          aria-pressed={recording}
          aria-label="Hold to speak a request"
          title={unsupported ? 'Microphone unavailable in this browser' : 'Hold to speak (or hold Space)'}
          className={`flex size-12 shrink-0 touch-none items-center justify-center rounded-full select-none ${
            recording ? 'recording-ring bg-bad text-bg' : 'bg-ok/15 text-ok ring-1 ring-ok/50 hover:bg-ok/25'
          }`}
        >
          {busy ? <Loader2 className="animate-spin" size={22} /> : <Mic size={22} />}
        </button>

        <form
          className="relative flex min-w-0 flex-1 items-center"
          onSubmit={(e) => {
            e.preventDefault()
            void submitTyped(text, true)
          }}
        >
          <input
            ref={typedRef}
            value={recording ? '' : text}
            onChange={(e) => setText(e.target.value)}
            readOnly={recording}
            placeholder={recording ? '' : authority.revoked ? 'The agent has no active grant. A request will be denied and logged.' : 'Hold the mic and speak, or type: Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM'}
            aria-label="Typed request"
            className="h-12 min-w-0 flex-1 rounded-xl bg-panel/70 pr-24 pl-4 text-[15px] text-fg ring-1 ring-line/70 placeholder:text-muted/70 focus:ring-info"
          />
          {(recording || busy) && (
            <span className="pointer-events-none absolute left-4 flex items-center gap-2 text-sm">
              {recording ? (
                <>
                  <span className="size-2 animate-pulse rounded-full bg-bad" />
                  <span className="font-medium text-bad">Listening</span>
                  <span className="font-mono text-muted">{rec.seconds}s</span>
                  <span className="text-muted">· release to send</span>
                </>
              ) : (
                <>
                  <Loader2 size={14} className="animate-spin text-info" />
                  <span className="font-medium text-info">Transcribing</span>
                  {slow && <span className="text-muted">· slow, you can type instead</span>}
                </>
              )}
            </span>
          )}
          <button type="submit" disabled={busy || !text.trim()} className="absolute right-1.5 flex h-9 items-center gap-1.5 rounded-lg bg-info px-3.5 text-sm font-semibold text-bg hover:brightness-110">
            <Send size={14} aria-hidden /> Send
          </button>
        </form>
      </div>

      {message && (
        <div role="alert" className="slot-in mt-3 flex items-start gap-2 rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad ring-1 ring-bad/30">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
          <span>{message}</span>
        </div>
      )}

      {intake && !message && (
        <div className="slot-in mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm">
          <span className="text-muted">Heard</span>
          <span className="font-medium"><Highlighted text={`“${intake.transcript}”`} terms={intake.low_confidence_terms} /></span>
          <span className="flex items-center gap-1.5 text-muted">
            {intake.parsed.sample_id ? <Mono className="text-fg">{intake.parsed.sample_id}</Mono> : <span className="text-warn">sample not found</span>}
            {intake.parsed.from ? <Mono>{intake.parsed.from}</Mono> : <span className="text-warn">from?</span>}
            <ArrowRight size={13} aria-hidden />
            {intake.parsed.to ? <Mono>{intake.parsed.to}</Mono> : <span className="text-warn">to?</span>}
          </span>
          {denied ? <Badge tone="bad" label="Denied by policy" /> : phase === 'clarify' ? <Badge tone="warn" icon={AlertTriangle} label="Needs clarification" /> : phase === 'ready' ? <Badge tone="ok" label="Understood" /> : null}
        </div>
      )}

      {intake && phase === 'clarify' && (
        <div className="slot-in mt-3 space-y-2.5 rounded-xl bg-panel/70 p-3.5 ring-1 ring-warn/30">
          <p className="text-sm text-warn">I can’t be sure about every part of that, so I won’t guess. Pick or correct it, then resubmit.</p>
          {intake.alternatives?.map((a) => (
            <div key={a.field} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted">Did you mean ({a.field.replace('_id', '')}):</span>
              {a.options.map((o) => (
                <button key={o} onClick={() => applyAlternative(a.field, o)} className="rounded-lg bg-surface px-2.5 py-1 font-mono text-xs ring-1 ring-line hover:ring-info">{o}</button>
              ))}
            </div>
          ))}
          <div className="flex gap-2">
            <input value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Edit transcript" className="h-10 min-w-0 flex-1 rounded-lg bg-surface px-3 text-sm ring-1 ring-line focus:ring-info" />
            <button onClick={() => void submitTyped(draft, false)} disabled={busy || !draft.trim()} className="h-10 rounded-lg bg-warn px-4 text-sm font-semibold text-bg hover:brightness-110">
              Resubmit corrected
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

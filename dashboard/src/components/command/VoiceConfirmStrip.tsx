import { Loader2, Mic, RotateCcw, Square, Volume2 } from 'lucide-react'
import { useNow } from '../../hooks/useNow'
import { useVoiceConfirm } from '../../hooks/useVoiceConfirm'
import { listenSeconds, type VoiceState } from '../../lib/voiceConfirm'

interface Props {
  transferId: string
  flowId: string
  audioUrl: string | null
  userId: string
  suspended: boolean
}

function Orb({ phase, level }: { phase: VoiceState['phase']; level: number }) {
  const listening = phase === 'listening'
  const tone = listening ? 'bg-ok/20 text-ok' : phase === 'speaking' ? 'bg-info/20 text-info' : 'bg-surface text-muted ring-1 ring-line'
  return (
    <span className={`relative flex size-10 shrink-0 items-center justify-center rounded-full ${tone}`} aria-hidden>
      {listening && <span className="absolute inset-0 rounded-full ring-2 ring-ok/60" style={{ transform: `scale(${1 + Math.min(level * 3, 0.6)})`, transition: 'transform 100ms linear' }} />}
      {phase === 'speaking' ? <Volume2 size={18} className="animate-pulse motion-reduce:animate-none" /> : phase === 'sending' || phase === 'starting' ? <Loader2 size={18} className="animate-spin motion-reduce:animate-none" /> : <Mic size={18} />}
    </span>
  )
}

const btn = 'h-8 shrink-0 rounded-lg px-3 text-[13px] font-medium ring-1 ring-line hover:bg-surface'

/**
 * Confirm the read-back by voice. The agent reads the request aloud; when it finishes, the microphone listens
 * for about 10 seconds for "confirm". What was said is judged by the backend, never here. The Confirm button
 * above always works too.
 */
export function VoiceConfirmStrip(props: Props) {
  const v = useVoiceConfirm(props)
  const now = useNow(250)
  const { state } = v
  const hasAudio = !!props.audioUrl

  const total = listenSeconds() * 1000
  const msLeft = state.phase === 'listening' ? Math.max(0, state.deadline - now) : 0
  const secondsLeft = Math.min(listenSeconds(), Math.ceil(msLeft / 1000))

  let headline = ''
  let sub: string | null = null
  let actions: React.ReactNode = null

  switch (state.phase) {
    case 'idle':
      headline = 'Or confirm by voice'
      sub = hasAudio ? 'I can read the request aloud, then listen for “confirm”.' : 'Press the button and say “confirm”.'
      actions = (
        <>
          {hasAudio && <button onClick={v.replay} className={btn}>Play read-back</button>}
          <button onClick={v.listenNow} className={btn}>Say “confirm”</button>
        </>
      )
      break
    case 'speaking':
      headline = 'Reading the request aloud…'
      sub = 'Then say “confirm” to go ahead.'
      actions = <button onClick={v.listenNow} className={btn}>Skip</button>
      break
    case 'blocked':
      headline = 'Tap to hear the request, then say “confirm”'
      sub = 'Your browser needs a tap before it will play audio.'
      actions = <button onClick={v.replay} className={`${btn} bg-info text-bg ring-0 hover:brightness-110`}>Hear it and answer</button>
      break
    case 'starting':
      headline = 'Waiting for the microphone…'
      break
    case 'listening':
      headline = 'Listening… say “confirm”'
      sub = state.heard ? 'Heard you, one moment…' : `I’ll stop listening in ${secondsLeft} s.`
      actions = (
        <button onClick={v.stop} className={`${btn} flex items-center gap-1.5`}>
          <Square size={12} aria-hidden /> Stop
        </button>
      )
      break
    case 'sending':
      headline = 'Checking what I heard…'
      break
    case 'confirming':
      headline = `Heard “${state.transcript}”. Confirming…`
      break
    case 'retry':
      headline = state.reason === 'silence' ? 'I didn’t hear anything.' : `I heard “${state.transcript ?? '…'}”. That wasn’t a confirmation.`
      sub = 'Say “confirm”, or press Confirm transfer.'
      actions = (
        <button onClick={v.listenNow} className={`${btn} flex items-center gap-1.5`}>
          <RotateCcw size={12} aria-hidden /> Listen again
        </button>
      )
      break
    case 'unavailable':
      headline = state.message
      break
    case 'error':
      headline = state.message
      actions = <button onClick={v.listenNow} className={btn}>Try again</button>
      break
  }

  return (
    <div role="group" aria-label="Confirm by voice" className="mt-3 rounded-xl bg-panel/70 px-3 py-2.5 ring-1 ring-line/60">
      <div className="flex items-center gap-3">
        <Orb phase={state.phase} level={v.level} />
        <div className="min-w-0 flex-1">
          <p aria-live="polite" className={`text-[13px] font-medium ${state.phase === 'error' || state.phase === 'unavailable' ? 'text-warn' : ''}`}>
            {headline}
          </p>
          {sub && <p className="text-xs text-muted">{sub}</p>}
        </div>
        <div className="flex items-center gap-2">{actions}</div>
      </div>
      {state.phase === 'listening' && (
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-line/70" aria-hidden>
          <div className="h-full rounded-full bg-ok transition-[width] duration-200 ease-linear" style={{ width: `${Math.min(100, (msLeft / total) * 100)}%` }} />
        </div>
      )}
      {hasAudio && state.phase !== 'unavailable' && (
        <button role="switch" aria-checked={v.auto} onClick={v.toggleAuto} className="mt-2 flex items-center gap-2 text-xs text-muted hover:text-fg">
          <span className={`inline-flex h-4 w-7 items-center rounded-full p-0.5 transition-colors ${v.auto ? 'bg-ok/70' : 'bg-line'}`}>
            <span className={`size-3 rounded-full bg-fg transition-transform ${v.auto ? 'translate-x-3' : ''}`} />
          </span>
          Read aloud and listen automatically
        </button>
      )}
    </div>
  )
}

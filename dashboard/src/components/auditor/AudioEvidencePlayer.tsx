import { Loader2, Pause, Play, ShieldCheck, Volume2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { ApiError, verifyAudio, voiceAudioUrl } from '../../lib/api'
import type { AudioVerification, VoiceRequestDoc } from '../../lib/types'
import { Badge, Mono } from '../ui/Badge'

/** Plays the original recording via the backend. Integrity is shown only if the backend reports it. */
export function AudioEvidencePlayer({ request }: { request: VoiceRequestDoc }) {
  const ref = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [failed, setFailed] = useState(false)
  const [checking, setChecking] = useState(false)
  const [check, setCheck] = useState<AudioVerification | null>(null)
  const [checkError, setCheckError] = useState<string | null>(null)

  // Integrity is only ever what the backend reports after re-hashing the stored audio.
  const verify = async () => {
    setChecking(true)
    setCheckError(null)
    try {
      setCheck(await verifyAudio(request._id))
    } catch (e) {
      setCheckError(e instanceof ApiError && e.kind === 'not_implemented' ? 'The backend can’t verify audio yet.' : e instanceof ApiError ? e.message : 'Verification failed.')
    } finally {
      setChecking(false)
    }
  }

  if (request.source === 'typed') {
    return <p className="text-sm text-muted">Typed request: there is no audio recording.</p>
  }

  const toggle = () => {
    const a = ref.current
    if (!a) return
    if (playing) a.pause()
    else void a.play().catch(() => setFailed(true))
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <button
          onClick={toggle}
          disabled={failed}
          className="flex items-center gap-1.5 rounded-md bg-info px-3 py-1.5 text-sm font-semibold text-bg hover:brightness-110"
        >
          {playing ? <Pause size={15} aria-hidden /> : <Play size={15} aria-hidden />}
          {playing ? 'Pause' : 'Play original audio'}
        </button>
        <Volume2 size={16} className="text-muted" aria-hidden />
        {request.audio_duration_secs != null && <span className="text-xs text-muted">{request.audio_duration_secs.toFixed(1)} s</span>}
      </div>
      <audio
        ref={ref}
        src={voiceAudioUrl(request._id)}
        preload="none"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => setFailed(true)}
      />
      {failed && <p role="alert" className="text-sm text-warn">Audio is unavailable or restricted for this request.</p>}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">SHA-256</span>
        <Mono className="break-all text-muted">{request.audio_sha256 ?? 'not recorded'}</Mono>
        {check ? (
          !check.has_audio ? <Badge tone="warn" label="No audio is stored for this request" /> :
          check.matches ? <Badge tone="ok" icon={ShieldCheck} label="Hash re-checked by the backend: unchanged" /> :
          <Badge tone="bad" label="Hash mismatch: the audio changed" />
        ) : request.audio_verified === true ? (
          <Badge tone="ok" label="Integrity verified by backend" />
        ) : request.audio_verified === false ? (
          <Badge tone="bad" label="Hash mismatch" />
        ) : (
          <Badge tone="mute" label="Integrity not checked yet" />
        )}
        <button onClick={() => void verify()} disabled={checking} className="flex items-center gap-1 rounded-md px-2 py-1 text-info ring-1 ring-line hover:bg-panel">
          {checking ? <Loader2 size={12} className="animate-spin" /> : <ShieldCheck size={12} aria-hidden />} Verify now
        </button>
        {checkError && <span role="alert" className="text-warn">{checkError}</span>}
      </div>
    </div>
  )
}

import { Pause, Play, Volume2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { voiceAudioUrl } from '../../lib/api'
import type { VoiceRequestDoc } from '../../lib/types'
import { Badge, Mono } from '../ui/Badge'

/** Plays the original recording via the backend. Integrity is shown only if the backend reports it. */
export function AudioEvidencePlayer({ request }: { request: VoiceRequestDoc }) {
  const ref = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [failed, setFailed] = useState(false)

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
        {request.audio_verified === true && <Badge tone="ok" label="INTEGRITY VERIFIED BY BACKEND" />}
        {request.audio_verified === false && <Badge tone="bad" label="HASH MISMATCH" />}
        {request.audio_verified == null && <Badge tone="mute" label="INTEGRITY CHECK NOT REPORTED" />}
      </div>
    </div>
  )
}

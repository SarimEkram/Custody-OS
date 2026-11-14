import { useCallback, useEffect, useRef, useState } from 'react'

export type RecorderStatus = 'idle' | 'recording' | 'unsupported' | 'denied' | 'error'

const MAX_SECONDS = 30 // matches the 30 s / 1 MB server cap in DATA_MODEL.md

export interface RecorderApi {
  status: RecorderStatus
  seconds: number
  error: string | null
  start: () => Promise<void>
  /** Resolves with the recorded audio, or null if nothing usable was captured. */
  stop: () => Promise<Blob | null>
}

export function isRecordingSupported(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined'
}

export function useMediaRecorder(): RecorderApi {
  const [status, setStatus] = useState<RecorderStatus>(isRecordingSupported() ? 'idle' : 'unsupported')
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const chunks = useRef<Blob[]>([])
  const tick = useRef<ReturnType<typeof setInterval> | null>(null)
  const startedAt = useRef(0)
  const pendingStop = useRef(false)
  const stopRef = useRef<() => Promise<Blob | null>>(() => Promise.resolve(null))

  const release = useCallback(() => {
    if (tick.current) clearInterval(tick.current)
    tick.current = null
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
  }, [])

  const stop = useCallback(
    () =>
      new Promise<Blob | null>((resolve) => {
        const rec = recorder.current
        if (!rec || rec.state === 'inactive') {
          pendingStop.current = true // released before getUserMedia resolved
          resolve(null)
          return
        }
        const heldMs = Date.now() - startedAt.current
        rec.onstop = () => {
          const blob = new Blob(chunks.current, { type: rec.mimeType || 'audio/webm' })
          release()
          recorder.current = null
          setStatus('idle')
          // Ignore accidental taps shorter than 400 ms.
          resolve(heldMs < 400 || blob.size === 0 ? null : blob)
        }
        rec.stop()
      }),
    [release],
  )
  useEffect(() => {
    stopRef.current = stop
  }, [stop])

  const start = useCallback(async () => {
    if (!isRecordingSupported()) {
      setStatus('unsupported')
      setError('This browser cannot record audio. Use the typed request box.')
      return
    }
    if (recorder.current) return
    pendingStop.current = false
    setError(null)
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (pendingStop.current) {
        media.getTracks().forEach((t) => t.stop())
        return
      }
      stream.current = media
      chunks.current = []
      const rec = new MediaRecorder(media)
      rec.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunks.current.push(ev.data)
      }
      rec.onerror = () => {
        release()
        recorder.current = null
        setStatus('error')
        setError('Recording failed. Use the typed request box.')
      }
      recorder.current = rec
      startedAt.current = Date.now()
      setSeconds(0)
      rec.start()
      setStatus('recording')
      tick.current = setInterval(() => {
        const s = Math.floor((Date.now() - startedAt.current) / 1000)
        setSeconds(s)
        if (s >= MAX_SECONDS) void stopRef.current()
      }, 250)
    } catch (e) {
      release()
      recorder.current = null
      const denied = e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'SecurityError')
      setStatus(denied ? 'denied' : 'error')
      setError(
        denied
          ? 'Microphone permission was denied. Allow it in the browser (localhost only), or use the typed request box.'
          : 'Could not start the microphone. Use the typed request box.',
      )
    }
  }, [release])

  useEffect(() => release, [release])

  return { status, seconds, error, start, stop }
}

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { ApiError, confirmByVoice } from '../lib/api'
import {
  createVad,
  DEFAULT_VAD,
  INITIAL_VOICE_STATE,
  listenSeconds,
  rmsLevel,
  setVoiceConfirmEnabled,
  voiceConfirmEnabled,
  voiceReducer,
  type VoiceState,
} from '../lib/voiceConfirm'

const NOT_YET = "Voice confirmation isn't available on this backend yet. Use the Confirm button."
const ACK_TIMEOUT_MS = 8000
const AFTER_SPEECH_PAUSE_MS = 350 // let the end of the agent's voice die away before the mic opens

// Once the backend says it can't do this, stop offering to try until the page is reloaded.
let backendLacksVoiceConfirm = false

interface Args {
  transferId: string
  /** Changes when a new read-back is issued for the same transfer, so each one gets its own spoken prompt. */
  flowId: string
  /** The spoken read-back (text-to-speech). Without it there is nothing to say first, so listening is manual. */
  audioUrl: string | null
  userId: string
  /** True while the Confirm button is being used, so the microphone is released. */
  suspended: boolean
}

export interface VoiceConfirm {
  state: VoiceState
  /** Live microphone level, 0-1, for the on-screen meter. */
  level: number
  /** Whether to read aloud and listen automatically for each read-back. */
  auto: boolean
  toggleAuto: () => void
  /** Play the spoken read-back, then listen. */
  replay: () => void
  /** Listen now, without reading aloud. */
  listenNow: () => void
  stop: () => void
}

/**
 * The agent reads the request aloud; when it finishes, the microphone listens for a short window for
 * "confirm". The clip is uploaded and the BACKEND decides whether it was a confirmation. This hook never
 * confirms anything itself: a "confirmed" reply just waits for the normal transfer update.
 */
export function useVoiceConfirm({ transferId, flowId, audioUrl, userId, suspended }: Args): VoiceConfirm {
  const [state, dispatch] = useReducer(voiceReducer, INITIAL_VOICE_STATE, (s): VoiceState => (backendLacksVoiceConfirm ? { phase: 'unavailable', message: NOT_YET } : s))
  const [auto, setAuto] = useState(voiceConfirmEnabled)
  const [level, setLevel] = useState(0)

  const token = useRef(0) // bumped to cancel any in-flight audio, microphone or upload work
  const audio = useRef<HTMLAudioElement | null>(null)
  const stopMic = useRef<() => void>(() => {})
  const startedFlow = useRef<string | null>(null)
  const user = useRef(userId)
  useEffect(() => {
    user.current = userId
  }, [userId])

  const stopAll = useCallback(() => {
    token.current += 1
    const a = audio.current
    if (a) {
      a.onended = null
      a.onerror = null
      a.pause()
      audio.current = null
    }
    stopMic.current()
    stopMic.current = () => {}
    setLevel(0)
  }, [])

  const submit = useCallback(
    async (clip: Blob, my: number) => {
      dispatch({ type: 'sending' })
      try {
        const r = await confirmByVoice(transferId, user.current, clip)
        if (my !== token.current) return
        if (r.confirmed) dispatch({ type: 'confirmed', transcript: r.transcript ?? 'confirm' })
        else dispatch({ type: 'retry', reason: 'not_recognized', transcript: r.transcript ?? null })
      } catch (e) {
        if (my !== token.current) return
        if (e instanceof ApiError && e.kind === 'not_implemented' && /^not found$/i.test(e.message)) {
          backendLacksVoiceConfirm = true // the route itself is missing (not just an unknown transfer)
          dispatch({ type: 'unavailable', message: NOT_YET })
        } else if (e instanceof ApiError && e.status === 422) {
          dispatch({ type: 'retry', reason: 'silence' })
        } else if (e instanceof ApiError && e.status === 503) {
          dispatch({ type: 'error', message: 'Speech-to-text is unavailable. Use the Confirm button.' })
        } else {
          dispatch({ type: 'error', message: e instanceof ApiError ? e.message : 'Voice confirmation failed. Use the Confirm button.' })
        }
      }
    },
    [transferId],
  )

  const listen = useCallback(
    async (my: number) => {
      if (backendLacksVoiceConfirm) return dispatch({ type: 'unavailable', message: NOT_YET })
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
        return dispatch({ type: 'unavailable', message: "This browser can't record audio. Use the Confirm button." })
      }
      dispatch({ type: 'starting' })
      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      } catch (e) {
        if (my !== token.current) return
        const denied = e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'SecurityError')
        return dispatch({
          type: 'unavailable',
          message: denied ? 'Microphone access is blocked. Allow it in the browser to confirm by voice, or use the Confirm button.' : 'No microphone was found. Use the Confirm button.',
        })
      }
      if (my !== token.current) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }

      const seconds = listenSeconds()
      const rec = new MediaRecorder(stream)
      const chunks: Blob[] = []
      rec.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunks.push(ev.data)
      }

      let ctx: AudioContext | null = null
      let analyser: AnalyserNode | null = null
      try {
        ctx = new AudioContext()
        void ctx.resume()
        analyser = ctx.createAnalyser()
        analyser.fftSize = 1024
        ctx.createMediaStreamSource(stream).connect(analyser)
      } catch {
        ctx = null
        analyser = null // no level meter: we still record for the whole window
      }

      const vad = createVad({ ...DEFAULT_VAD, maxMs: seconds * 1000 })
      const samples = new Uint8Array(analyser?.fftSize ?? 0)
      const startedAt = Date.now()
      let finished = false
      let announced = false
      let timer: ReturnType<typeof setInterval> | undefined

      const release = () => {
        if (timer) clearInterval(timer)
        stream.getTracks().forEach((t) => t.stop())
        void ctx?.close().catch(() => {})
        setLevel(0)
      }
      stopMic.current = () => {
        if (finished) return
        finished = true
        rec.onstop = null
        try {
          if (rec.state !== 'inactive') rec.stop()
        } catch {
          /* already stopped */
        }
        release()
      }
      const finish = (heard: boolean) => {
        if (finished) return
        finished = true
        rec.onstop = () => {
          release()
          if (my !== token.current) return
          if (!heard) return dispatch({ type: 'retry', reason: 'silence' })
          void submit(new Blob(chunks, { type: rec.mimeType || 'audio/webm' }), my)
        }
        try {
          rec.stop()
        } catch {
          release()
          if (my === token.current) dispatch({ type: 'retry', reason: 'silence' })
        }
      }

      rec.start()
      dispatch({ type: 'listening', deadline: startedAt + seconds * 1000 })
      timer = setInterval(() => {
        const now = Date.now()
        if (!analyser) {
          if (now - startedAt >= seconds * 1000) finish(true) // can't tell silence from speech: send the clip
          return
        }
        analyser.getByteTimeDomainData(samples)
        const l = rmsLevel(samples)
        setLevel(l)
        const decision = vad.push(l, now)
        if (vad.heard && !announced) {
          announced = true
          dispatch({ type: 'heard_speech' })
        }
        if (decision !== 'listen') finish(vad.heard)
      }, 100)
    },
    [submit],
  )

  const begin = useCallback(
    (speakFirst: boolean) => {
      stopAll()
      const my = token.current
      if (speakFirst && audioUrl) {
        const a = new Audio(audioUrl)
        audio.current = a
        dispatch({ type: 'speaking' })
        a.onended = () => {
          if (my !== token.current) return
          audio.current = null
          setTimeout(() => {
            if (my === token.current) void listen(my)
          }, AFTER_SPEECH_PAUSE_MS)
        }
        a.onerror = () => {
          if (my !== token.current) return
          audio.current = null
          dispatch({ type: 'reset' }) // no spoken read-back: the text and the manual button still work
        }
        a.play().catch((err: unknown) => {
          if (my !== token.current) return
          audio.current = null
          dispatch(err instanceof DOMException && err.name === 'NotAllowedError' ? { type: 'blocked' } : { type: 'reset' })
        })
      } else {
        void listen(my)
      }
    },
    [audioUrl, listen, stopAll],
  )

  // Each new read-back is read aloud and listened for, once.
  useEffect(() => {
    if (!auto || suspended || !audioUrl || backendLacksVoiceConfirm) return
    const key = `${transferId}:${flowId}`
    if (startedFlow.current === key) return
    startedFlow.current = key
    begin(true)
  }, [auto, suspended, audioUrl, transferId, flowId, begin])

  // Using the Confirm button (or leaving the card) releases the microphone and silences the agent.
  useEffect(() => {
    if (!suspended) return
    stopAll()
    dispatch({ type: 'reset' })
  }, [suspended, stopAll])
  useEffect(() => stopAll, [stopAll])

  // "Confirmed" only means the backend accepted it. If the transfer update never follows, say so.
  const confirming = state.phase === 'confirming'
  useEffect(() => {
    if (!confirming) return
    const t = setTimeout(() => dispatch({ type: 'error', message: "The backend accepted it, but the transfer hasn't updated yet. Press Confirm transfer." }), ACK_TIMEOUT_MS)
    return () => clearTimeout(t)
  }, [confirming])

  const toggleAuto = useCallback(() => {
    const next = !auto
    setVoiceConfirmEnabled(next)
    setAuto(next)
    if (!next) {
      stopAll()
      dispatch({ type: 'reset' })
    } else {
      startedFlow.current = null // a read-back that is already showing gets read aloud now
    }
  }, [auto, stopAll])

  return {
    state,
    level,
    auto,
    toggleAuto,
    replay: useCallback(() => begin(true), [begin]),
    listenNow: useCallback(() => begin(false), [begin]),
    stop: useCallback(() => {
      stopAll()
      dispatch({ type: 'reset' })
    }, [stopAll]),
  }
}

// Voice confirmation of a read-back: the agent speaks the request, then the dashboard listens for a short
// window for the person to say "confirm". This file is the pure part: settings, end-of-speech detection
// and the state machine. The browser pieces (audio, microphone) live in hooks/useVoiceConfirm.ts.
//
// What the dashboard does NOT do: decide that what was said counts as a confirmation. The clip is uploaded
// and the backend decides (and records what it heard). Identity comes from the logged-in user, never the voice.

export const DEFAULT_LISTEN_SECONDS = 10
const MIN_LISTEN = 3
const MAX_LISTEN = 30

const store = (): Storage | null => {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

/** How long to listen after the agent finishes speaking. 10 s unless a saved setting says otherwise (3-30). */
export function listenSeconds(): number {
  const raw = store()?.getItem('custody.voiceListenSeconds')
  const n = raw == null ? NaN : Number(raw)
  return Number.isFinite(n) && n >= MIN_LISTEN && n <= MAX_LISTEN ? Math.round(n) : DEFAULT_LISTEN_SECONDS
}

/** Voice confirmation is on unless the person switched it off. */
export const voiceConfirmEnabled = (): boolean => store()?.getItem('custody.voiceConfirm') !== '0'

export function setVoiceConfirmEnabled(on: boolean): void {
  try {
    store()?.setItem('custody.voiceConfirm', on ? '1' : '0')
  } catch {
    /* private mode: the choice just won't persist */
  }
}

// ---------- end-of-speech detection ----------

export interface VadOptions {
  /** RMS level (0-1) above which we call it speech. */
  threshold: number
  /** Silence after speech that ends the clip early. */
  silenceMs: number
  /** Hard stop for the whole listening window. */
  maxMs: number
  /** How much loud audio counts as "the person spoke" rather than a click or breath. */
  minSpeechMs: number
}

export type VadDecision = 'listen' | 'end_after_speech' | 'timeout_with_speech' | 'timeout_silence'

export interface Vad {
  push: (level: number, nowMs: number) => VadDecision
  readonly heard: boolean
}

export const DEFAULT_VAD: Omit<VadOptions, 'maxMs'> = { threshold: 0.035, silenceMs: 1200, minSpeechMs: 250 }

/** Feed it one audio level at a time. It says when to stop recording, and whether anything was said. */
export function createVad(opts: VadOptions): Vad {
  let startedAt: number | null = null
  let prev: number | null = null
  let loudMs = 0
  let lastLoud: number | null = null

  return {
    get heard() {
      return loudMs >= opts.minSpeechMs
    },
    push(level, nowMs) {
      if (startedAt === null) startedAt = nowMs
      const dt = prev === null ? 0 : Math.max(0, nowMs - prev)
      prev = nowMs
      if (level >= opts.threshold) {
        loudMs += dt
        lastLoud = nowMs
      }
      const heard = loudMs >= opts.minSpeechMs
      if (heard && lastLoud !== null && nowMs - lastLoud >= opts.silenceMs) return 'end_after_speech'
      if (nowMs - startedAt >= opts.maxMs) return heard ? 'timeout_with_speech' : 'timeout_silence'
      return 'listen'
    },
  }
}

/** Root-mean-square level (0-1) of 8-bit time-domain audio samples (128 is silence). */
export function rmsLevel(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0
  let sum = 0
  for (let i = 0; i < samples.length; i++) {
    const v = (samples[i] - 128) / 128
    sum += v * v
  }
  return Math.sqrt(sum / samples.length)
}

// ---------- state machine ----------

export type VoiceState =
  | { phase: 'idle' }
  | { phase: 'speaking' }
  | { phase: 'blocked' } // the browser would not autoplay the spoken read-back
  | { phase: 'starting' } // waiting for the microphone
  | { phase: 'listening'; deadline: number; heard: boolean }
  | { phase: 'sending' }
  | { phase: 'confirming'; transcript: string } // backend said yes; waiting for the transfer update
  | { phase: 'retry'; reason: 'not_recognized' | 'silence'; transcript: string | null }
  | { phase: 'unavailable'; message: string } // not going to work this session; use the button
  | { phase: 'error'; message: string }

export type VoiceAction =
  | { type: 'reset' }
  | { type: 'speaking' }
  | { type: 'blocked' }
  | { type: 'starting' }
  | { type: 'listening'; deadline: number }
  | { type: 'heard_speech' }
  | { type: 'sending' }
  | { type: 'confirmed'; transcript: string }
  | { type: 'retry'; reason: 'not_recognized' | 'silence'; transcript?: string | null }
  | { type: 'unavailable'; message: string }
  | { type: 'error'; message: string }

export const INITIAL_VOICE_STATE: VoiceState = { phase: 'idle' }

export function voiceReducer(state: VoiceState, a: VoiceAction): VoiceState {
  switch (a.type) {
    case 'reset':
      return INITIAL_VOICE_STATE
    case 'speaking':
      return { phase: 'speaking' }
    case 'blocked':
      return { phase: 'blocked' }
    case 'starting':
      return { phase: 'starting' }
    case 'listening':
      return { phase: 'listening', deadline: a.deadline, heard: false }
    case 'heard_speech':
      // Only meaningful while listening; a late event must not revive a finished attempt.
      return state.phase === 'listening' && !state.heard ? { ...state, heard: true } : state
    case 'sending':
      return { phase: 'sending' }
    case 'confirmed':
      return { phase: 'confirming', transcript: a.transcript }
    case 'retry':
      return { phase: 'retry', reason: a.reason, transcript: a.transcript ?? null }
    case 'unavailable':
      return { phase: 'unavailable', message: a.message }
    case 'error':
      return { phase: 'error', message: a.message }
  }
}

/** True while the microphone or the agent's voice is in use, so the UI can offer to stop. */
export const isBusy = (s: VoiceState): boolean => s.phase === 'speaking' || s.phase === 'starting' || s.phase === 'listening' || s.phase === 'sending'

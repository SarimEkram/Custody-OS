import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, confirmByVoice } from './api'
import { createVad, DEFAULT_LISTEN_SECONDS, DEFAULT_VAD, INITIAL_VOICE_STATE, isBusy, listenSeconds, rmsLevel, voiceConfirmEnabled, voiceReducer } from './voiceConfirm'

const stubStorage = (data: Record<string, string>) =>
  vi.stubGlobal('localStorage', { getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => void (data[k] = v) })

describe('listening window', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('is 10 seconds by default', () => {
    expect(DEFAULT_LISTEN_SECONDS).toBe(10)
    expect(listenSeconds()).toBe(10)
  })

  it('honours a saved setting inside 3-30 s and ignores nonsense', () => {
    stubStorage({ 'custody.voiceListenSeconds': '5' })
    expect(listenSeconds()).toBe(5)
    stubStorage({ 'custody.voiceListenSeconds': '120' })
    expect(listenSeconds()).toBe(10)
    stubStorage({ 'custody.voiceListenSeconds': 'abc' })
    expect(listenSeconds()).toBe(10)
  })

  it('is on unless the person turned it off', () => {
    expect(voiceConfirmEnabled()).toBe(true)
    stubStorage({ 'custody.voiceConfirm': '0' })
    expect(voiceConfirmEnabled()).toBe(false)
  })
})

describe('end-of-speech detection', () => {
  const opts = { ...DEFAULT_VAD, maxMs: 10_000 }
  // Drive a Vad with (level, ms) frames every 100 ms; return the first non-"listen" decision and when.
  const run = (frames: number[]) => {
    const vad = createVad(opts)
    for (let i = 0; i < frames.length; i++) {
      const d = vad.push(frames[i], i * 100)
      if (d !== 'listen') return { d, at: i * 100, heard: vad.heard }
    }
    return { d: 'listen' as const, at: null, heard: vad.heard }
  }
  const loud = (n: number) => Array(n).fill(0.2)
  const quiet = (n: number) => Array(n).fill(0)

  it('ends early once the person has spoken and then paused', () => {
    const r = run([...quiet(2), ...loud(8), ...quiet(20)]) // 0.8 s of speech, then silence
    expect(r.d).toBe('end_after_speech')
    expect(r.heard).toBe(true)
    expect(r.at).toBeLessThan(3000) // long before the 10 s window is up
  })

  it('does not stop mid-sentence for a short pause', () => {
    const r = run([...loud(5), ...quiet(5), ...loud(5), ...quiet(20)]) // a 0.5 s gap is not the end
    expect(r.d).toBe('end_after_speech')
    expect(r.at).toBeGreaterThan(1500) // it waited through the pause and ended after the second burst
  })

  it('does not count a brief click or breath as speech', () => {
    const r = run([...quiet(5), 0.2, ...quiet(120)])
    expect(r.heard).toBe(false)
    expect(r.d).toBe('timeout_silence')
  })

  it('times out with no speech after the whole window', () => {
    const r = run(quiet(150))
    expect(r.d).toBe('timeout_silence')
    expect(r.at).toBe(10_000)
  })

  it('times out WITH speech if they never stop (e.g. a noisy room)', () => {
    const r = run(loud(150))
    expect(r.d).toBe('timeout_with_speech')
    expect(r.at).toBe(10_000)
  })

  it('keeps listening inside the window', () => {
    expect(run(quiet(50)).d).toBe('listen')
  })

  it('measures level from 8-bit samples (128 is silence)', () => {
    expect(rmsLevel(new Uint8Array(256).fill(128))).toBe(0)
    expect(rmsLevel(new Uint8Array(256).map((_, i) => (i % 2 ? 255 : 1)))).toBeGreaterThan(0.9)
    expect(rmsLevel([])).toBe(0)
  })
})

describe('voice confirmation state machine', () => {
  const s = (...actions: Parameters<typeof voiceReducer>[1][]) => actions.reduce(voiceReducer, INITIAL_VOICE_STATE)

  it('goes speaking -> starting -> listening -> sending -> confirming on the happy path', () => {
    expect(s({ type: 'speaking' }).phase).toBe('speaking')
    expect(s({ type: 'speaking' }, { type: 'starting' }, { type: 'listening', deadline: 1000 }).phase).toBe('listening')
    const done = s({ type: 'starting' }, { type: 'listening', deadline: 1 }, { type: 'sending' }, { type: 'confirmed', transcript: 'confirm' })
    expect(done).toEqual({ phase: 'confirming', transcript: 'confirm' })
  })

  it('remembers that speech was heard, but only while listening', () => {
    const listening = s({ type: 'listening', deadline: 1 })
    expect(voiceReducer(listening, { type: 'heard_speech' })).toMatchObject({ phase: 'listening', heard: true })
    // a late event after the attempt ended must not revive it
    const sending = s({ type: 'listening', deadline: 1 }, { type: 'sending' })
    expect(voiceReducer(sending, { type: 'heard_speech' })).toBe(sending)
  })

  it('offers a retry with what was heard, or after silence', () => {
    expect(s({ type: 'retry', reason: 'not_recognized', transcript: 'cancel' })).toEqual({ phase: 'retry', reason: 'not_recognized', transcript: 'cancel' })
    expect(s({ type: 'retry', reason: 'silence' })).toEqual({ phase: 'retry', reason: 'silence', transcript: null })
  })

  it('reports blocked autoplay, unavailable and errors distinctly, and can reset', () => {
    expect(s({ type: 'blocked' }).phase).toBe('blocked')
    expect(s({ type: 'unavailable', message: 'x' })).toEqual({ phase: 'unavailable', message: 'x' })
    expect(s({ type: 'error', message: 'y' })).toEqual({ phase: 'error', message: 'y' })
    expect(s({ type: 'error', message: 'y' }, { type: 'reset' })).toEqual(INITIAL_VOICE_STATE)
  })

  it('knows when the microphone or the agent voice is in use', () => {
    expect(isBusy(s({ type: 'listening', deadline: 1 }))).toBe(true)
    expect(isBusy(s({ type: 'speaking' }))).toBe(true)
    expect(isBusy(s({ type: 'retry', reason: 'silence' }))).toBe(false)
    expect(isBusy(INITIAL_VOICE_STATE)).toBe(false)
  })
})

describe('confirmByVoice request', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uploads the clip with the logged-in user, never anything the voice said', async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ confirmed: true, transcript: 'confirm' }), { status: 200 }))
    vi.stubGlobal('fetch', f)
    const r = await confirmByVoice('TR-0031', 'user:jlee', new Blob(['x'], { type: 'audio/webm' }))
    expect(r).toEqual({ confirmed: true, transcript: 'confirm', reason: undefined })
    const [url, init] = f.mock.calls[0]
    expect(String(url)).toMatch(/\/transfers\/TR-0031\/confirm\/voice$/)
    expect(init.method).toBe('POST')
    const form = init.body as FormData
    expect(form.get('user_id')).toBe('user:jlee')
    expect(form.get('audio')).toBeInstanceOf(Blob)
  })

  it('passes through a "not a confirmation" answer with what was heard', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ confirmed: false, transcript: 'cancel', reason: 'not_recognized' }), { status: 200 })))
    expect(await confirmByVoice('TR-1', 'user:jlee', new Blob(['x']))).toMatchObject({ confirmed: false, transcript: 'cancel', reason: 'not_recognized' })
  })

  it('rejects a response that does not say whether it was confirmed', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 })))
    await expect(confirmByVoice('TR-1', 'user:jlee', new Blob(['x']))).rejects.toMatchObject({ kind: 'invalid' })
  })

  it('reports a backend without the route as not_implemented with the stock "Not Found"', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 })))
    const e = await confirmByVoice('TR-1', 'user:jlee', new Blob(['x'])).catch((x) => x)
    expect(e).toBeInstanceOf(ApiError)
    expect(e.kind).toBe('not_implemented')
    expect(e.message).toBe('Not Found')
  })

  it('surfaces the backend\'s readable refusal (e.g. not the requester)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'only the person who made the request can confirm' }), { status: 403 })))
    await expect(confirmByVoice('TR-1', 'user:mokafor', new Blob(['x']))).rejects.toMatchObject({ status: 403, message: 'only the person who made the request can confirm' })
  })
})

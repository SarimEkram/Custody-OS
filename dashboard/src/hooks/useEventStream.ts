import { useEffect } from 'react'
import { checkHealth, getSnapshot, streamUrl } from '../lib/api'
import { parseStreamEvent } from '../lib/normalize'
import { useCustodyStore } from '../store/custodyStore'

const MAX_BACKOFF_MS = 10_000

/**
 * One SSE connection for the whole app. Reconnects with backoff, re-syncs from
 * GET /state (when the backend has it) after every (re)connect, and relies on
 * id-based dedup in the store so replays never create duplicate rows.
 * Mount exactly once (in AppShell).
 */
export function useEventStream(): void {
  useEffect(() => {
    const { applyEvent, hydrate, setConnection, setBackend } = useCustodyStore.getState()
    let es: EventSource | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let disposed = false
    let everConnected = false

    const sync = async () => {
      try {
        const snap = await getSnapshot()
        if (snap && !disposed) hydrate(snap)
      } catch {
        /* snapshot is best-effort; the live stream still works */
      }
      const health = await checkHealth()
      if (!disposed) setBackend(health)
    }

    const connect = () => {
      if (disposed) return
      setConnection(everConnected ? 'reconnecting' : 'connecting')
      es = new EventSource(streamUrl())
      es.onopen = () => {
        attempt = 0
        everConnected = true
        setConnection('connected')
        void sync()
      }
      es.onmessage = (m: MessageEvent<string>) => {
        const ev = parseStreamEvent(m.data)
        if (ev) applyEvent(ev)
      }
      es.onerror = () => {
        es?.close()
        es = null
        if (disposed) return
        setConnection(attempt >= 3 ? 'disconnected' : 'reconnecting')
        void checkHealth().then((h) => !disposed && setBackend(h))
        const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS)
        attempt += 1
        timer = setTimeout(connect, delay)
      }
    }

    connect()
    return () => {
      disposed = true
      if (timer) clearTimeout(timer)
      es?.close()
    }
  }, [])
}

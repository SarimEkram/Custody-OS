import { pickActiveTransfer, useCustodyStore } from '../store/custodyStore'
import { TERMINAL_STATES } from '../lib/types'
import type { Tone } from '../lib/format'

export interface AgentStatus {
  label: string
  tone: Tone
  /** True while the agent may still take actions (Stop must stay reachable). */
  operating: boolean
}

export function useAgentStatus(): AgentStatus {
  const t = useCustodyStore((s) => pickActiveTransfer(s))
  if (!t) return { label: 'Idle', tone: 'mute', operating: false }
  if (TERMINAL_STATES.includes(t.state)) return { label: 'Idle', tone: 'mute', operating: false }
  switch (t.state) {
    case 'blocked':
      return { label: t.blocked_reason === 'readback_pending' ? 'Waiting for confirmation' : 'Waiting for approval', tone: 'warn', operating: true }
    case 'revoked':
      return { label: 'Halted: authority revoked', tone: 'bad', operating: true }
    case 'recovering':
      return { label: 'Recovering sample', tone: 'warn', operating: true }
    default:
      return { label: 'Working', tone: 'ok', operating: true }
  }
}

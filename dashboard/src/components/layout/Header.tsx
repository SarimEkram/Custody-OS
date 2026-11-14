import { ClipboardCheck, FlaskConical, LayoutDashboard, ShieldCheck } from 'lucide-react'
import { useAgentStatus } from '../../hooks/useAgentStatus'
import { IDENTITIES } from '../../lib/seed'
import { useCustodyStore, type View } from '../../store/custodyStore'
import { Badge } from '../ui/Badge'

const NAV: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: 'control', label: 'Control Center', icon: LayoutDashboard },
  { id: 'auditor', label: 'Auditor', icon: ClipboardCheck },
  { id: 'demo', label: 'Demo & Settings', icon: FlaskConical },
]

export function ConnectionBadge() {
  const connection = useCustodyStore((s) => s.connection)
  const backend = useCustodyStore((s) => s.backend)
  if (connection === 'connected') return <Badge tone="ok" label="Live" title={`Event stream connected. Backend: ${backend}`} />
  if (connection === 'disconnected') return <Badge tone="bad" label="Backend offline" title="Cannot reach the agent service" />
  return <Badge tone="warn" label={connection === 'connecting' ? 'Connecting' : 'Reconnecting'} />
}

export function IdentitySwitcher() {
  const identityId = useCustodyStore((s) => s.identityId)
  const setIdentity = useCustodyStore((s) => s.setIdentity)
  const me = IDENTITIES.find((i) => i._id === identityId) ?? IDENTITIES[0]
  return (
    <label className="flex items-center gap-2.5" title="Demo identity only. This is not production authentication.">
      <span className="hidden text-xs leading-tight text-muted xl:block">
        Demo identity
      </span>
      <span className="relative flex items-center">
        <span aria-hidden className="pointer-events-none absolute left-1.5 flex size-6 items-center justify-center rounded-full bg-info/20 text-[11px] font-semibold text-info">
          {me.name.charAt(0)}
        </span>
        <select
          value={identityId}
          onChange={(e) => setIdentity(e.target.value)}
          aria-label="Demo identity"
          className="h-9 appearance-none rounded-lg bg-panel pr-3 pl-9 text-sm font-medium text-fg ring-1 ring-line/70 hover:ring-line"
        >
          {IDENTITIES.map((i) => (
            <option key={i._id} value={i._id}>
              {i.name} · {i.roleLabel}
            </option>
          ))}
        </select>
      </span>
    </label>
  )
}

export function Header() {
  const view = useCustodyStore((s) => s.view)
  const setView = useCustodyStore((s) => s.setView)
  const scripted = useCustodyStore((s) => s.scripted)
  const status = useAgentStatus()

  return (
    <header className="flex h-14 shrink-0 items-center gap-6 border-b border-line/60 bg-surface/80 px-5 backdrop-blur">
      <div className="flex items-center gap-2.5">
        <span className="flex size-7 items-center justify-center rounded-lg bg-ok/15 text-ok">
          <ShieldCheck size={17} aria-hidden />
        </span>
        <span className="text-[17px] font-semibold tracking-tight">CustodyOS</span>
      </div>
      <nav className="flex items-center gap-1 rounded-xl bg-bg/70 p-1 ring-1 ring-line/50" aria-label="Views">
        {NAV.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setView(id)}
            aria-current={view === id ? 'page' : undefined}
            className={`flex h-8 items-center gap-2 rounded-lg px-3 text-sm font-medium ${view === id ? 'bg-panel text-fg shadow-sm ring-1 ring-line/60' : 'text-muted hover:text-fg'}`}
          >
            <Icon size={15} aria-hidden />
            <span className="hidden md:inline">{label}</span>
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-4">
        {scripted && <Badge tone="info" label="Scripted replay" title="DEMO_SCRIPTED=1: replaying recorded tool calls, no model call" />}
        <span className="flex items-center gap-2 text-sm">
          <span className="text-muted">Agent</span>
          <Badge tone={status.tone} label={status.label} />
        </span>
        <ConnectionBadge />
        <IdentitySwitcher />
      </div>
    </header>
  )
}

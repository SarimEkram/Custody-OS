import { Component, type ErrorInfo, type ReactNode } from 'react'
import { RestoreAuthorityDialog } from '../components/command/RestoreAuthorityDialog'
import { Header } from '../components/layout/Header'
import { StopAgentButton } from '../components/workflow/StopAgentButton'
import { useAgentStatus } from '../hooks/useAgentStatus'
import { useEventStream } from '../hooks/useEventStream'
import { AuditorPage } from '../pages/AuditorPage'
import { ControlCenter } from '../pages/ControlCenter'
import { DemoPage } from '../pages/DemoPage'
import { useCustodyStore } from '../store/custodyStore'

class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) {
    return { error }
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack)
  }
  render() {
    if (this.state.error) {
      return (
        <div role="alert" className="m-6 rounded-lg border border-bad/40 bg-bad/10 p-4">
          <p className="font-semibold text-bad">Something went wrong rendering this view.</p>
          <p className="mt-1 text-sm text-muted">{this.state.error.message}</p>
          <button onClick={() => this.setState({ error: null })} className="mt-3 rounded-md border border-line px-3 py-1.5 text-sm">Try again</button>
        </div>
      )
    }
    return this.props.children
  }
}

function OfflineBanner() {
  const connection = useCustodyStore((s) => s.connection)
  if (connection === 'connected' || connection === 'connecting') return null
  return (
    <div role="status" className="border-b border-warn/40 bg-warn/10 px-4 py-1.5 text-sm text-warn">
      {connection === 'reconnecting' ? 'Reconnecting to the agent service…' : 'The agent service is unreachable. The typed request box and views stay available, but nothing is live until it reconnects.'}
    </div>
  )
}

export function AppShell() {
  useEventStream()
  const view = useCustodyStore((s) => s.view)
  const status = useAgentStatus()
  return (
    <div className="flex h-full min-h-screen flex-col">
      <Header />
      <OfflineBanner />
      <Boundary>
        <main className="flex min-h-0 flex-1 flex-col">
          {view === 'control' && <ControlCenter />}
          {view === 'auditor' && <AuditorPage />}
          {view === 'demo' && <DemoPage />}
        </main>
      </Boundary>
      <RestoreAuthorityDialog />
      {/* Stop stays reachable from every view while the agent is operating. */}
      {status.operating && view !== 'control' && (
        <div className="fixed right-4 bottom-4 z-30 w-56"><StopAgentButton compact /></div>
      )}
    </div>
  )
}

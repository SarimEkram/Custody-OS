import { Copy, FileAudio, Loader2, Play, RotateCcw } from 'lucide-react'
import { useRef, useState } from 'react'
import { ApiError, sendTypedRequest, sendVoiceRequest } from '../../lib/api'
import { IDENTITIES } from '../../lib/seed'
import { useCustodyStore } from '../../store/custodyStore'
import { ConnectionBadge } from '../layout/Header'
import { Badge, Mono } from '../ui/Badge'
import { Panel } from '../ui/Panel'

interface Scenario {
  id: number
  title: string
  phrase: string
  identity: string
  sample: string
  expected: string[]
  note?: string
}

const SCENARIOS: Scenario[] = [
  {
    id: 1,
    title: 'Normal transfer',
    phrase: 'Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM.',
    identity: 'user:jlee',
    sample: 'TS-1042',
    expected: ['Intake (voice or typed)', 'Policy checks', 'Reservation + courier assigned', 'Read-back → Confirm', 'Checkout: SENT → CONFIRMED by scanner', 'Courier moves along the corridor', 'Intake confirmed at SEQ-3', 'State: received'],
  },
  {
    id: 2,
    title: 'Restricted evidence',
    phrase: 'Move EV-2201 to the Forensics Lab now.',
    identity: 'user:jlee',
    sample: 'EV-2201',
    expected: ['Request by Jordan Lee', 'Forensic restriction detected', 'Checkout: PENDING APPROVAL (agent pauses)', 'Switch identity to M. Okafor → Approve', 'Workflow resumes via SSE', 'Scanner confirms custody actions'],
  },
  {
    id: 3,
    title: 'Mid-transfer revocation',
    phrase: 'Move TS-1077 from Freezer A to Sequencing Station 3.',
    identity: 'user:jlee',
    sample: 'TS-1077',
    note: 'After the courier is moving, press “Revoke grant” (Control Center) or run: python scripts/revoke_grant.py GRANT-0007',
    expected: ['Sample checked out (scanner-verified)', 'Courier moving, 3-minute exposure clock running', 'GRANT-0007 revoked → HALT row', 'Reservation and notice compensated', 'Recovery route shown, return freezer selected', 'Scanner confirms check-in', 'Exposure outcome recorded'],
  },
]

export const RESET_COMMAND = 'scripts/reset_demo.sh'

export function DemoDirector() {
  const identityId = useCustodyStore((s) => s.identityId)
  const setIdentity = useCustodyStore((s) => s.setIdentity)
  const setView = useCustodyStore((s) => s.setView)
  const setLastIntake = useCustodyStore((s) => s.setLastIntake)
  const setActiveTransfer = useCustodyStore((s) => s.setActiveTransfer)
  const backend = useCustodyStore((s) => s.backend)
  const scripted = useCustodyStore((s) => s.scripted)
  const connection = useCustodyStore((s) => s.connection)

  const [busy, setBusy] = useState<number | 'clip' | null>(null)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null)
  const [copied, setCopied] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const me = IDENTITIES.find((i) => i._id === identityId)

  const accept = (r: Awaited<ReturnType<typeof sendTypedRequest>>) => {
    setLastIntake(r)
    if (r.transfer_id) setActiveTransfer(r.transfer_id)
    setView('control')
  }

  const launch = async (s: Scenario) => {
    setBusy(s.id)
    setMsg(null)
    setIdentity(s.identity)
    try {
      accept(await sendTypedRequest(s.phrase, s.identity))
    } catch (e) {
      setMsg({ tone: 'bad', text: e instanceof ApiError ? e.message : 'Could not start the scenario.' })
    } finally {
      setBusy(null)
    }
  }

  const uploadClip = async (file: File) => {
    setBusy('clip')
    setMsg(null)
    try {
      accept(await sendVoiceRequest(file, identityId))
    } catch (e) {
      setMsg({ tone: 'bad', text: e instanceof ApiError ? e.message : 'Could not upload the clip.' })
    } finally {
      setBusy(null)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(RESET_COMMAND)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable; the command is visible to copy by hand */
    }
  }

  return (
    <div className="grid gap-3 lg:grid-cols-[2fr_1fr]">
      <div className="space-y-3">
        {SCENARIOS.map((s) => (
          <Panel
            key={s.id}
            title={`Scenario ${s.id} — ${s.title}`}
            actions={
              <button
                onClick={() => void launch(s)}
                disabled={busy !== null || connection === 'disconnected'}
                className="flex items-center gap-1.5 rounded-md bg-ok px-3 py-1.5 text-sm font-bold text-bg hover:brightness-110"
              >
                {busy === s.id ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} aria-hidden />} Start (typed)
              </button>
            }
          >
            <p className="text-sm">“{s.phrase}”</p>
            <p className="mt-1 text-xs text-muted">Runs as <b>{IDENTITIES.find((i) => i._id === s.identity)?.name}</b> through the normal request path and gate. Sample <Mono>{s.sample}</Mono>.</p>
            <ol className="mt-3 flex flex-wrap gap-1.5">
              {s.expected.map((x, n) => (
                <li key={x} className="rounded-full bg-panel px-2.5 py-1 text-xs text-muted"><span className="mr-1 font-mono text-fg/60">{n + 1}</span>{x}</li>
              ))}
            </ol>
            {s.note && <p className="mt-2 text-sm text-warn">{s.note}</p>}
          </Panel>
        ))}
        {msg && <p role="alert" className={`text-sm ${msg.tone === 'bad' ? 'text-bad' : 'text-ok'}`}>{msg.text}</p>}
      </div>

      <div className="space-y-3">
        <Panel title="Status">
          <dl className="space-y-2 text-sm">
            <div className="flex items-center justify-between"><dt className="text-muted">Event stream</dt><dd><ConnectionBadge /></dd></div>
            <div className="flex items-center justify-between">
              <dt className="text-muted">Backend health</dt>
              <dd>{backend === 'up' ? <Badge tone="ok" label="UP" /> : backend === 'down' ? <Badge tone="bad" label="DOWN" /> : <Badge tone="mute" label="UNKNOWN (no /health)" />}</dd>
            </div>
            <div className="flex items-center justify-between"><dt className="text-muted">Demo identity</dt><dd>{me?.name} · {me?.roleLabel}</dd></div>
            <div className="flex items-center justify-between"><dt className="text-muted">Agent mode</dt><dd>{scripted ? <Badge tone="info" label="SCRIPTED (DEMO_SCRIPTED=1)" /> : <Badge tone="ok" label="LIVE AI" />}</dd></div>
          </dl>
        </Panel>

        <Panel title="Reset demo" icon={<RotateCcw size={16} className="text-warn" aria-hidden />}>
          <p className="text-sm text-muted">The dashboard never resets data itself. There is no safe reset endpoint, so run this from the repo root:</p>
          <div className="mt-2 flex items-center gap-2 rounded-md bg-bg p-2">
            <code className="flex-1 font-mono text-sm">{RESET_COMMAND}</code>
            <button onClick={() => void copy()} aria-label="Copy reset command" className="rounded p-1 text-muted hover:text-fg"><Copy size={15} /></button>
          </div>
          {copied && <p className="mt-1 text-xs text-ok">Copied.</p>}
          <p className="mt-2 text-xs text-muted">Then reload this page. Reset target: under 15 s.</p>
        </Panel>

        <Panel title="Backup assets" icon={<FileAudio size={16} className="text-info" aria-hidden />}>
          <p className="text-sm text-muted">Noisy room? Send a pre-recorded clip (e.g. <Mono>demo_audio/beat1.webm</Mono>) through the same voice path and gate.</p>
          <input ref={fileRef} type="file" accept="audio/*" className="sr-only" id="clip" onChange={(e) => e.target.files?.[0] && void uploadClip(e.target.files[0])} />
          <label htmlFor="clip" className="mt-2 inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-line px-3 py-2 text-sm hover:border-info">
            {busy === 'clip' ? <Loader2 size={15} className="animate-spin" /> : <FileAudio size={15} aria-hidden />} Upload clip
          </label>
          <p className="mt-2 text-xs text-muted">Also keep: the recorded backup video, <Mono>DEMO_SCRIPTED=1</Mono> mode, and a phone hotspot.</p>
        </Panel>
      </div>
    </div>
  )
}

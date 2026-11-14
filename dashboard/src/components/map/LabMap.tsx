import { Building2, FlaskConical, Snowflake, Truck, X, type LucideIcon } from 'lucide-react'
import { useMemo } from 'react'
import { stateLabel, stateTone } from '../../lib/format'
import { scanKey } from '../../lib/normalize'
import { plannedRoute, polyline, recoveryRoute, routeUpTo, type Pt } from '../../lib/route'
import type { LabLocation, LedgerEntry, ScanEvent, Transfer } from '../../lib/types'
import { activeExposure, pickActiveTransfer, useCustodyStore, verifiedLocation } from '../../store/custodyStore'
import { Badge, Mono } from '../ui/Badge'
import { MissionBar } from '../workflow/MissionBar'

const W = 760
const H = 420
const ROOM_W = 200
const ROOM_H = 88

const C = { ok: '#59e1c1', warn: '#f2b86b', bad: '#f06b78', info: '#7aa9f8', mute: '#4b5d75', room: '#13233a', line: '#29384d' }
const KIND_ICON: Record<LabLocation['kind'], LucideIcon> = { freezer: Snowflake, station: FlaskConical, lab: Building2, corridor: Truck }

type RouteStyle = { color: string; dash?: string; solid?: boolean; flow?: boolean; label: string }

/** Route colour follows the documented states. "Solid mint" is reserved for scanner-verified legs. */
function routeStyle(t: Transfer | null): RouteStyle {
  if (!t) return { color: C.mute, dash: '3 7', label: 'No active transfer' }
  switch (t.state) {
    case 'requested':
    case 'reserved':
      return { color: C.mute, dash: '3 7', label: 'Planned route' }
    case 'checked_out':
    case 'in_transit':
      return { color: C.ok, dash: '10 8', flow: true, label: 'Authorized route' }
    case 'blocked':
      return { color: C.warn, dash: '10 8', label: 'Paused for a decision' }
    case 'revoked':
      return { color: C.bad, dash: '10 8', label: 'Authority revoked' }
    case 'recovering':
    case 'recovered':
      return { color: C.bad, dash: '3 7', label: 'Original route abandoned' }
    case 'received':
      return { color: C.ok, solid: true, label: 'Handoff verified' }
    case 'cancelled':
      return { color: C.mute, dash: '3 7', label: 'Cancelled' }
  }
}

function returnFreezer(t: Transfer, ledger: LedgerEntry[]): string {
  for (let i = ledger.length - 1; i >= 0; i--) {
    const e = ledger[i]
    if (e.transfer_id !== t._id || e.entry_type !== 'recovery') continue
    const id = (e.args?.freezer_id ?? e.response?.freezer_id) as unknown
    if (typeof id === 'string') return id
  }
  return t.from
}

const hasScan = (scans: ScanEvent[], sample: string, types: ScanEvent['type'][], loc?: string) =>
  scans.some((s) => s.sample_id === sample && types.includes(s.type) && (!loc || s.location_id === loc))

export function LabMap() {
  const locations = useCustodyStore((s) => s.locations)
  const samples = useCustodyStore((s) => s.samples)
  const scans = useCustodyStore((s) => s.scans)
  const ledger = useCustodyStore((s) => s.ledger)
  const courierMap = useCustodyStore((s) => s.courier)
  const transfer = useCustodyStore((s) => pickActiveTransfer(s))
  const selSample = useCustodyStore((s) => s.selectedSample)
  const selLoc = useCustodyStore((s) => s.selectedLocation)
  const selectSample = useCustodyStore((s) => s.selectSample)
  const selectLocation = useCustodyStore((s) => s.selectLocation)
  const selectEntry = useCustodyStore((s) => s.selectEntry)

  const loc = (id: string): LabLocation | undefined => locations.find((l) => l._id === id)
  const corridor = loc('CORR-1')
  const style = routeStyle(transfer)
  const courier = transfer ? courierMap[transfer._id] : undefined
  const recovering = transfer?.state === 'recovering' || transfer?.state === 'recovered'
  const moving = !!transfer && !!courier && (transfer.state === 'in_transit' || transfer.state === 'recovering')
  const lastScan = scans[scans.length - 1]

  const placed = useMemo(() => {
    const byLoc: Record<string, string[]> = {}
    for (const s of Object.values(samples)) {
      const v = verifiedLocation(s._id, scans, s.location_id)
      ;(byLoc[v.location] ??= []).push(s._id)
    }
    return byLoc
  }, [samples, scans])

  const from = transfer ? loc(transfer.from) : undefined
  const to = transfer ? loc(transfer.to) : undefined
  const back = transfer ? loc(returnFreezer(transfer, ledger)) : undefined

  const planned: Pt[] = from && to && corridor ? plannedRoute(from.map, to.map, corridor.map) : []
  const recovery: Pt[] = recovering && back && corridor ? recoveryRoute(courier ?? corridor.map, back.map, corridor.map) : []
  const travelled: Pt[] = moving && !recovering && courier ? routeUpTo(planned, courier) : []
  const verifiedOut = transfer ? hasScan(scans, transfer.sample_id, ['checkout', 'handoff']) : false
  const verifiedIn = transfer ? hasScan(scans, transfer.sample_id, ['intake'], transfer.to) : false

  const openCheckpoint = () => {
    if (!transfer) return
    for (let i = ledger.length - 1; i >= 0; i--) {
      if (ledger[i].transfer_id === transfer._id && ledger[i].entry_type === 'decision') return selectEntry(ledger[i]._id)
    }
  }

  const selSampleObj = selSample ? samples[selSample] : undefined
  const selLocObj = selLoc ? loc(selLoc) : undefined
  const name = (id: string) => loc(id)?.name ?? id

  return (
    <section className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-xl bg-surface ring-1 ring-line/60">
      {/* what is happening, in one line */}
      <header className="flex items-center justify-between gap-3 px-5 pt-4">
        <div className="min-w-0">
          {transfer ? (
            <>
              <h2 className="truncate text-lg font-semibold tracking-tight">
                <Mono className="text-fg">{transfer.sample_id}</Mono>
                <span className="mx-2 font-normal text-muted">{name(transfer.from)} to {name(transfer.to)}</span>
              </h2>
              <p className="text-[13px] text-muted">{style.label}</p>
            </>
          ) : (
            <>
              <h2 className="text-lg font-semibold tracking-tight">Laboratory floor</h2>
              <p className="text-[13px] text-muted">Speak or type a request to start a transfer.</p>
            </>
          )}
        </div>
        {transfer && <Badge tone={stateTone(transfer.state)} label={stateLabel(transfer.state)} />}
      </header>

      <div className="relative min-h-0 flex-1 px-3">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-full w-full" role="img" aria-label="Laboratory floor map" preserveAspectRatio="xMidYMid meet">
          <defs>
            <pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="1" fill="#29384d" opacity="0.55" />
            </pattern>
            <marker id="arrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M1 1 L9 5 L1 9" fill="none" stroke={C.warn} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </marker>
          </defs>
          <rect width={W} height={H} fill="url(#dots)" />

          {/* corridor lane */}
          {corridor && (
            <g>
              <rect x={40} y={corridor.map.y - 20} width={W - 80} height={40} rx={20} fill="#0e1a2c" stroke={C.line} strokeWidth={1} />
              <text x={W - 62} y={corridor.map.y + 4} textAnchor="end" fill={C.mute} fontSize={11}>Corridor</text>
            </g>
          )}

          {/* routes */}
          {planned.length > 1 && (
            <g fill="none" strokeLinecap="round" strokeLinejoin="round">
              <polyline points={polyline(planned)} stroke={style.color} strokeWidth={style.solid ? 4 : 3} strokeDasharray={style.solid ? undefined : style.dash} className={style.flow ? 'route-flow' : undefined} opacity={recovering ? 0.35 : 0.9} />
              {travelled.length > 1 && <polyline points={polyline(travelled)} stroke={C.ok} strokeWidth={5} opacity={0.9} />}
              {verifiedOut && !moving && !recovering && transfer?.state !== 'received' && <polyline points={polyline(planned.slice(0, 2))} stroke={C.ok} strokeWidth={4} />}
              {verifiedIn && <polyline points={polyline(planned)} stroke={C.ok} strokeWidth={4} />}
            </g>
          )}
          {recovery.length > 1 && (
            <polyline points={polyline(recovery)} fill="none" stroke={C.warn} strokeWidth={4} strokeDasharray="10 8" strokeLinecap="round" strokeLinejoin="round" className="route-flow" markerEnd="url(#arrow)" />
          )}

          {/* policy checkpoint on the first leg */}
          {transfer && planned.length > 1 && (
            <g transform={`translate(${planned[0].x} ${(planned[0].y + planned[1].y) / 2})`} onClick={openCheckpoint} className="cursor-pointer" role="button" aria-label="Open the latest policy decision">
              <circle r={11} fill={C.room} stroke={C.info} strokeWidth={2} />
              <path d="M0 -5 L4 -3 V1 C4 3.5 2 5 0 6 C-2 5 -4 3.5 -4 1 V-3 Z" fill="none" stroke={C.info} strokeWidth={1.5} strokeLinejoin="round" />
              <title>Policy checkpoint. Click to open the latest decision.</title>
            </g>
          )}

          {/* rooms */}
          {locations.map((l) => {
            if (l.kind === 'corridor') return null
            const here = placed[l._id] ?? []
            const isDest = !!transfer && l._id === transfer.to && !['received', 'recovered', 'cancelled'].includes(transfer.state)
            const isBack = recovery.length > 0 && l._id === back?._id
            const verified = transfer ? hasScan(scans, transfer.sample_id, ['checkout', 'handoff', 'intake', 'checkin'], l._id) : false
            const selected = selLoc === l._id
            const Icon = KIND_ICON[l.kind]
            const x = l.map.x - ROOM_W / 2
            const y = l.map.y - ROOM_H / 2
            return (
              <g key={l._id} className="cursor-pointer" onClick={() => selectLocation(selected ? null : l._id)} role="button" aria-label={`${l.name}, ${here.length} samples`}>
                <rect x={x} y={y} width={ROOM_W} height={ROOM_H} rx={14} fill={C.room} stroke={selected ? C.info : isBack ? C.warn : isDest ? C.ok : C.line} strokeWidth={selected || isDest || isBack ? 2 : 1.25} />
                <foreignObject x={x + 14} y={y + 12} width={ROOM_W - 44} height={44}>
                  <div className="flex items-center gap-2 text-fg" style={{ fontFamily: 'var(--font-sans)' }}>
                    <Icon size={15} className="shrink-0 text-muted" aria-hidden />
                    <div className="min-w-0 leading-tight">
                      <div className="text-[13px] font-semibold whitespace-nowrap">{l.name}</div>
                      <div className="font-mono text-[10px] text-muted">{l._id}</div>
                    </div>
                  </div>
                </foreignObject>
                {(isDest || isBack) && (
                  <text x={x + ROOM_W - 14} y={y + ROOM_H - 10} textAnchor="end" fontSize={10} fontWeight={600} fill={isBack ? C.warn : C.ok}>{isBack ? 'Return point' : 'Destination'}</text>
                )}
                {/* scanner point */}
                <g transform={`translate(${x + ROOM_W - 18} ${y + 18})`}>
                  <circle r={6} fill="none" stroke={verified ? C.ok : C.mute} strokeWidth={2} />
                  {verified && <circle r={2.5} fill={C.ok} />}
                  {lastScan?.location_id === l._id && <circle key={scanKey(lastScan)} r={6} fill="none" stroke={C.ok} className="scan-ping" />}
                  <title>{verified ? 'Scanner verified custody here' : 'Scanner point, nothing verified yet'}</title>
                </g>
                {/* samples (scanner-verified positions) */}
                {here.map((id, i) => {
                  const r = samples[id]?.restriction
                  const dot = r === 'forensic' ? C.bad : r === 'controlled' ? C.warn : C.info
                  return (
                    <g key={id} transform={`translate(${x + 14 + i * 76} ${y + ROOM_H - 24})`} onClick={(ev) => { ev.stopPropagation(); selectSample(selSample === id ? null : id) }}>
                      <rect width={68} height={18} rx={9} fill={selSample === id ? '#22364f' : '#192b44'} stroke={selSample === id ? '#fff' : 'none'} />
                      <circle cx={10} cy={9} r={4} fill={dot} />
                      <text x={19} y={13} fill="#f1f5f9" fontSize={10} fontFamily="var(--font-mono)">{id}</text>
                    </g>
                  )
                })}
              </g>
            )
          })}

          {/* courier: glides between 400 ms ticks */}
          {moving && courier && (
            <g className="glide" style={{ transform: `translate(${courier.x}px, ${courier.y}px)` }}>
              <circle r={15} fill={recovering ? C.warn : C.ok} opacity={0.18} />
              <circle r={9} fill="#0b1220" stroke={recovering ? C.warn : C.ok} strokeWidth={3} />
              <circle r={3} fill={recovering ? C.warn : C.ok} />
            </g>
          )}
        </svg>

        {(selSampleObj || selLocObj) && (
          <aside className="slot-in absolute right-4 bottom-3 w-72 rounded-xl bg-panel p-4 text-sm shadow-2xl ring-1 ring-line">
            <button onClick={() => (selSampleObj ? selectSample(null) : selectLocation(null))} className="absolute top-3 right-3 text-muted hover:text-fg" aria-label="Close details">
              <X size={16} />
            </button>
            {selSampleObj && <SampleDetails id={selSampleObj._id} />}
            {selLocObj && (
              <div>
                <h3 className="font-semibold">{selLocObj.name}</h3>
                <Mono className="text-muted">{selLocObj._id}</Mono>
                <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                  <dt className="text-muted">Certified for</dt>
                  <dd>{selLocObj.certifications.join(', ') || 'none'}</dd>
                  <dt className="text-muted">Samples here</dt>
                  <dd>{(placed[selLocObj._id] ?? []).map((id) => <Mono key={id} className="mr-2">{id}</Mono>)}{(placed[selLocObj._id] ?? []).length === 0 && 'none'}</dd>
                  <dt className="text-muted">Forensic</dt>
                  <dd>{selLocObj.certifications.includes('FORENSIC') ? 'Accepted' : 'Not accepted'}</dd>
                </dl>
              </div>
            )}
          </aside>
        )}
      </div>

      <div className="px-4 pb-4">
        <MissionBar />
      </div>
    </section>
  )
}

function SampleDetails({ id }: { id: string }) {
  const sample = useCustodyStore((s) => s.samples[id])
  const scans = useCustodyStore((s) => s.scans)
  const transfers = useCustodyStore((s) => s.transfers)
  const locations = useCustodyStore((s) => s.locations)
  const v = verifiedLocation(id, scans, sample.location_id)
  const t = Object.values(transfers).find((x) => x.sample_id === id && !['received', 'recovered', 'cancelled'].includes(x.state))
  const exp = activeExposure(id, scans)
  return (
    <div>
      <h3 className="font-semibold"><Mono>{id}</Mono></h3>
      <p className="text-muted">{sample.description}</p>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted">Type</dt><dd className="capitalize">{sample.type}</dd>
        <dt className="text-muted">Restriction</dt><dd className="capitalize">{sample.restriction}</dd>
        <dt className="text-muted">Verified at</dt><dd>{locations.find((l) => l._id === v.location)?.name ?? v.location}</dd>
        <dt className="text-muted">Custodian</dt><dd>{v.custodian ?? `location:${v.location}`}</dd>
        <dt className="text-muted">Transfer</dt><dd>{t ? <Mono>{t._id}</Mono> : 'none'}</dd>
        <dt className="text-muted">Time limit</dt><dd>{sample.max_minutes_out} min out{exp ? ' (clock running)' : ''}</dd>
      </dl>
    </div>
  )
}

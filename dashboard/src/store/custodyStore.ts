// Backend is the source of truth. This store only mirrors what the SSE stream and
// REST responses report; it never decides authorization or marks anything verified.
import { create } from 'zustand'
import { resolveCourier } from '../lib/courier'
import { mergeById, scanKey } from '../lib/normalize'
import { applyGrantEvidence } from '../lib/authority'
import { isRunEnd } from '../lib/runEnd'
import { DEFAULT_GRANT_ID, SEED_LOCATIONS, SEED_SAMPLES } from '../lib/seed'
import type {
  Approval,
  ConfirmationRequest,
  ConnectionStatus,
  CourierPosition,
  Grant,
  IntakeResponse,
  LabLocation,
  LedgerEntry,
  Sample,
  ScanEvent,
  Snapshot,
  StreamEvent,
  Transfer,
} from '../lib/types'

export type View = 'control' | 'auditor' | 'demo'
export type BackendStatus = 'unknown' | 'up' | 'degraded' | 'down'
export type StopPhase = 'idle' | 'requested' | 'failed'

const LEDGER_CAP = 500

/** States where nothing more can be confirmed or approved for the transfer. */
export const STOPPED_STATES: readonly string[] = ['revoked', 'recovering', 'recovered', 'cancelled', 'received']

const byTime = <T extends { timestamp: string }>(t: T) => t.timestamp

interface CustodyState {
  view: View
  identityId: string
  connection: ConnectionStatus
  backend: BackendStatus
  scripted: boolean
  demoSpeed: number | null

  locations: LabLocation[]
  samples: Record<string, Sample>
  grants: Record<string, Grant>
  transfers: Record<string, Transfer>
  ledger: LedgerEntry[]
  scans: ScanEvent[]
  approvals: Record<string, Approval>
  readbacks: Record<string, ConfirmationRequest>
  courier: Record<string, CourierPosition>

  activeTransferId: string | null
  selectedSample: string | null
  selectedLocation: string | null
  selectedEntryId: string | null
  lastIntake: IntakeResponse | null
  stopPhase: StopPhase
  stopTransferId: string | null
  stopError: string | null
  restoreOpen: boolean
  /** Set when the backend refuses a request because the requester has no active grant. */
  noGrantAt: string | null

  setView: (v: View) => void
  setIdentity: (id: string) => void
  setConnection: (c: ConnectionStatus) => void
  setBackend: (b: BackendStatus) => void
  setActiveTransfer: (id: string | null) => void
  selectSample: (id: string | null) => void
  selectLocation: (id: string | null) => void
  selectEntry: (id: string | null) => void
  setLastIntake: (r: IntakeResponse | null) => void
  setStopPhase: (p: StopPhase, transferId?: string | null, error?: string | null) => void
  setRestoreOpen: (open: boolean) => void
  setNoGrant: (at: string | null) => void
  clearReadback: (transferId: string) => void
  applyEvent: (e: StreamEvent) => void
  hydrate: (s: Snapshot) => void
}

export const useCustodyStore = create<CustodyState>((set) => ({
  view: 'control',
  identityId: 'user:jlee',
  connection: 'connecting',
  backend: 'unknown',
  scripted: false,
  demoSpeed: null,

  locations: SEED_LOCATIONS,
  samples: Object.fromEntries(SEED_SAMPLES.map((s) => [s._id, s])),
  grants: {},
  transfers: {},
  ledger: [],
  scans: [],
  approvals: {},
  readbacks: {},
  courier: {},

  activeTransferId: null,
  selectedSample: null,
  selectedLocation: null,
  selectedEntryId: null,
  lastIntake: null,
  stopPhase: 'idle',
  stopTransferId: null,
  stopError: null,
  restoreOpen: false,
  noGrantAt: null,

  setView: (view) => set({ view }),
  setIdentity: (identityId) => set({ identityId }),
  setConnection: (connection) => set({ connection }),
  setBackend: (backend) => set({ backend }),
  setActiveTransfer: (activeTransferId) => set({ activeTransferId }),
  selectSample: (selectedSample) => set({ selectedSample, selectedLocation: null }),
  selectLocation: (selectedLocation) => set({ selectedLocation, selectedSample: null }),
  selectEntry: (selectedEntryId) => set({ selectedEntryId }),
  setLastIntake: (lastIntake) => set({ lastIntake }),
  setStopPhase: (stopPhase, stopTransferId = null, stopError = null) => set({ stopPhase, stopTransferId, stopError }),
  setRestoreOpen: (restoreOpen) => set({ restoreOpen }),
  setNoGrant: (noGrantAt) => set({ noGrantAt }),
  clearReadback: (transferId) =>
    set((st) => {
      const { [transferId]: _removed, ...rest } = st.readbacks
      void _removed
      return { readbacks: rest }
    }),

  applyEvent: (e) =>
    set((st) => {
      switch (e.kind) {
        case 'ledger': {
          const ledger = mergeById(st.ledger, [e.data], (x) => x._id, byTime).slice(-LEDGER_CAP)
          if (ledger === st.ledger) return {}
          const patch: Partial<CustodyState> = { ledger }
          const grants = applyGrantEvidence(st.grants, e.data)
          if (grants !== st.grants) patch.grants = grants
          // A new grant ends the "no active grant" refusal: the person can send a request again right away.
          if (e.data.rule_hits?.includes('grant_reissued')) patch.noGrantAt = null
          // A halt entry is the backend acknowledging the stop.
          if (e.data.decision === 'halt' && !isRunEnd(e.data) && st.stopPhase === 'requested') patch.stopPhase = 'idle'
          return patch
        }
        case 'transfer': {
          const t = e.data
          const patch: Partial<CustodyState> = { transfers: { ...st.transfers, [t._id]: t } }
          if (!st.activeTransferId || t.state === 'requested') patch.activeTransferId = t._id
          // A read-back is only actionable while the transfer is still waiting on it.
          if (t._id in st.readbacks && (t.readback_confirmed || t.halt || STOPPED_STATES.includes(t.state))) {
            const { [t._id]: _r, ...rest } = st.readbacks
            void _r
            patch.readbacks = rest
          }
          if (t.state === 'revoked' || t.state === 'recovering') patch.stopPhase = 'idle'
          return patch
        }
        case 'scan': {
          const scans = mergeById(st.scans, [e.data], scanKey, (x) => x.scanned_at)
          return scans === st.scans ? {} : { scans }
        }
        case 'approval':
          return { approvals: { ...st.approvals, [e.data._id]: e.data } }
        case 'confirmation_request':
          return { readbacks: { ...st.readbacks, [e.data.transfer_id]: e.data } }
        case 'courier_position': {
          const next = resolveCourier(e.data, st.locations, st.courier[e.data.transfer_id])
          return next ? { courier: { ...st.courier, [e.data.transfer_id]: next } } : {}
        }
      }
    }),

  hydrate: (s) =>
    set((st) => {
      const patch: Partial<CustodyState> = {}
      if (s.locations?.length) patch.locations = s.locations
      if (s.samples?.length) patch.samples = { ...st.samples, ...Object.fromEntries(s.samples.map((x) => [x._id, x])) }
      if (s.grants?.length) patch.grants = Object.fromEntries(s.grants.map((g) => [g._id, g]))
      if (s.transfers?.length) patch.transfers = { ...st.transfers, ...Object.fromEntries(s.transfers.map((t) => [t._id, t])) }
      if (s.approvals?.length) patch.approvals = { ...st.approvals, ...Object.fromEntries(s.approvals.map((a) => [a._id, a])) }
      if (s.ledger?.length) patch.ledger = mergeById(st.ledger, s.ledger, (x) => x._id, byTime).slice(-LEDGER_CAP)
      if (s.scans?.length) patch.scans = mergeById(st.scans, s.scans, scanKey, (x) => x.scanned_at)
      if (typeof s.demo_scripted === 'boolean') patch.scripted = s.demo_scripted
      if (typeof s.demo_speed === 'number') patch.demoSpeed = s.demo_speed
      return patch
    }),
}))

export { DEFAULT_GRANT_ID }

// ---------- selectors (pure, derived from verified evidence) ----------

/** The most relevant transfer: the explicitly active one, else the most recently updated non-terminal one. */
export function pickActiveTransfer(st: Pick<CustodyState, 'transfers' | 'activeTransferId'>): Transfer | null {
  if (st.activeTransferId && st.transfers[st.activeTransferId]) return st.transfers[st.activeTransferId]
  const all = Object.values(st.transfers)
  return all.length ? all[all.length - 1] : null
}

/**
 * Where the sample is VERIFIED to be: the latest scanner event for it, else the seed/snapshot location.
 * A service saying "sent" never moves the sample.
 */
export function verifiedLocation(sampleId: string, scans: ScanEvent[], fallback: string): { location: string; custodian?: string; scan?: ScanEvent } {
  for (let i = scans.length - 1; i >= 0; i--) {
    const s = scans[i]
    if (s.sample_id === sampleId) return { location: s.location_id, custodian: s.to_custodian, scan: s }
  }
  return { location: fallback }
}

/** Active exposure for a sample: last verified checkout with no later check-in. */
export function activeExposure(sampleId: string, scans: ScanEvent[]): { checkedOutAt: string } | null {
  let out: string | null = null
  for (const s of scans) {
    if (s.sample_id !== sampleId) continue
    if (s.type === 'checkout') out = s.scanned_at
    if (s.type === 'checkin') out = null
  }
  return out ? { checkedOutAt: out } : null
}

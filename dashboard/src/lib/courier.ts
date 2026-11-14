// The backend reports the courier as a fraction (`progress`) of its CURRENT leg plus that leg's `from` and
// `to`, not as coordinates. This turns that into a point on the map.
//
// Two legs matter:
//  - Outbound: from !== to. Position = `progress` of the way along the room -> corridor -> room route.
//  - Recovery ("back to origin" reroute): the courier service sets destination = origin, so it reports
//    from === to and a progress that restarts at 0. The backend now also sends `original_to` and
//    `return_from_progress`, which pin down where it turned back (and survive a page reload). If they are
//    absent (older backend), the leg's start is the last place we drew the courier (`recoveryStart`).
import { plannedRoute, pointAt, recoveryRoute } from './route'
import type { CourierPosition, CourierSignal, LabLocation } from './types'

export function resolveCourier(signal: CourierSignal, locations: LabLocation[], prev?: CourierPosition): CourierPosition | null {
  const progress = Math.min(1, Math.max(0, signal.progress))

  // Coordinates supplied directly: trust them.
  if (typeof signal.x === 'number' && typeof signal.y === 'number') {
    return { transfer_id: signal.transfer_id, x: signal.x, y: signal.y, progress, recoveryStart: prev?.recoveryStart }
  }

  const loc = (id?: string) => locations.find((l) => l._id === id)
  const corridor = loc('CORR-1')
  const from = loc(signal.from)
  const to = loc(signal.to)
  if (!corridor || !from || !to) return prev ?? null

  if (from._id !== to._id) {
    const p = pointAt(plannedRoute(from.map, to.map, corridor.map), progress)
    return { transfer_id: signal.transfer_id, x: p.x, y: p.y, progress }
  }

  // Recovery leg: from === to. Where did it turn back? Best: the backend says so (original_to + return_from_progress),
  // which survives a page reload. Otherwise: where we last drew the courier.
  const orig = signal.original_to ? loc(signal.original_to) : undefined
  const fromBackend =
    orig && typeof signal.return_from_progress === 'number'
      ? pointAt(plannedRoute(from.map, orig.map, corridor.map), Math.min(1, Math.max(0, signal.return_from_progress)))
      : undefined
  const start = fromBackend ?? prev?.recoveryStart ?? (prev ? { x: prev.x, y: prev.y } : undefined)
  if (!start) return prev ?? null // never saw it move, so we can't know where the leg began
  const p = pointAt(recoveryRoute(start, to.map, corridor.map), progress)
  return { transfer_id: signal.transfer_id, x: p.x, y: p.y, progress, recoveryStart: start }
}

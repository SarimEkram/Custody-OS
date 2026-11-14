// Map geometry. Routes run room → corridor → room as right-angle paths so they read like a
// floor plan instead of crossing diagonals. Pure functions, easy to test.
export interface Pt {
  x: number
  y: number
}

const same = (a: Pt, b: Pt) => Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5

function dedupe(points: Pt[]): Pt[] {
  return points.filter((p, i) => i === 0 || !same(p, points[i - 1]))
}

/** from → down/up to the corridor lane → along it → up/down into the destination. */
export function plannedRoute(from: Pt, to: Pt, corridor: Pt): Pt[] {
  return dedupe([from, { x: from.x, y: corridor.y }, { x: to.x, y: corridor.y }, to])
}

/** Back to a freezer from wherever the courier currently is. */
export function recoveryRoute(pos: Pt, back: Pt, corridor: Pt): Pt[] {
  const pts: Pt[] = [pos]
  if (Math.abs(pos.y - corridor.y) > 1) pts.push({ x: pos.x, y: corridor.y })
  pts.push({ x: back.x, y: corridor.y }, back)
  return dedupe(pts)
}

export const polyline = (pts: Pt[]): string => pts.map((p) => `${p.x},${p.y}`).join(' ')

const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y)

function onSegment(p: Pt, a: Pt, b: Pt, tol = 3): boolean {
  return dist(a, p) + dist(p, b) - dist(a, b) < tol
}

/** The part of a route the courier has already covered, ending at its current position. */
export function routeUpTo(points: Pt[], pos: Pt): Pt[] {
  for (let i = 0; i < points.length - 1; i++) {
    if (onSegment(pos, points[i], points[i + 1])) return dedupe([...points.slice(0, i + 1), pos])
  }
  return []
}

export function routeLength(points: Pt[]): number {
  let total = 0
  for (let i = 0; i < points.length - 1; i++) total += dist(points[i], points[i + 1])
  return total
}

/** The point `fraction` (0-1) of the way along a route, measured by path length. */
export function pointAt(points: Pt[], fraction: number): Pt {
  if (points.length === 0) return { x: 0, y: 0 }
  const total = routeLength(points)
  if (total === 0) return points[0]
  let remaining = Math.min(1, Math.max(0, fraction)) * total
  for (let i = 0; i < points.length - 1; i++) {
    const seg = dist(points[i], points[i + 1])
    if (remaining <= seg) {
      const k = seg === 0 ? 0 : remaining / seg
      return { x: points[i].x + (points[i + 1].x - points[i].x) * k, y: points[i].y + (points[i + 1].y - points[i].y) * k }
    }
    remaining -= seg
  }
  return points[points.length - 1]
}

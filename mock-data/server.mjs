// MOCK agent service for rehearsing the dashboard. NOT the real backend.
// Zero dependencies: `node mock-data/server.mjs` (Node 18+). Listens on :8000 so the
// Vite proxy works unchanged. Everything here is simulated; nothing is written anywhere.
//
// It follows the documented contracts (ARCHITECTURE.md / DATA_MODEL.md):
//   decision entry BEFORE outcome, outcome status "sent", then a scanner event, then a
//   verification entry. State only advances after the scan.
import http from 'node:http'

const PORT = Number(process.env.PORT ?? 8000)
const SPEED = Number(process.env.MOCK_SPEED ?? 1) // 2 = twice as fast, 0.5 = slower

const sleep = (ms) => new Promise((r) => setTimeout(r, ms / SPEED))
const now = () => new Date().toISOString()

// ---------- reference data (matches DEMO.md seed) ----------
const LOCATIONS = [
  { _id: 'FZ-A', name: 'Freezer A', kind: 'freezer', certifications: ['BSL-2', 'FORENSIC'], map: { x: 110, y: 90 } },
  { _id: 'FZ-B', name: 'Freezer B', kind: 'freezer', certifications: ['BSL-2', 'FORENSIC'], map: { x: 110, y: 330 } },
  { _id: 'CORR-1', name: 'Corridor', kind: 'corridor', certifications: [], map: { x: 380, y: 210 } },
  { _id: 'SEQ-3', name: 'Sequencing Station 3', kind: 'station', certifications: ['BSL-2'], map: { x: 650, y: 90 } },
  { _id: 'FOR-LAB', name: 'Forensics Lab', kind: 'lab', certifications: ['FORENSIC'], map: { x: 650, y: 330 } },
]
const loc = (id) => LOCATIONS.find((l) => l._id === id)

const SAMPLES_SEED = {
  'TS-1042': { _id: 'TS-1042', type: 'tissue', description: 'Tissue sample', restriction: 'standard', location_id: 'FZ-B', max_minutes_out: 20, status: 'stored', checked_out_at: null, defaultTo: 'SEQ-3' },
  'EV-2201': { _id: 'EV-2201', type: 'evidence', description: 'DNA swab, case 24-1187', restriction: 'forensic', location_id: 'FZ-B', max_minutes_out: 20, status: 'stored', checked_out_at: null, defaultTo: 'FOR-LAB' },
  'TS-1077': { _id: 'TS-1077', type: 'tissue', description: 'Tissue sample (short clock)', restriction: 'controlled', location_id: 'FZ-A', max_minutes_out: 3, status: 'stored', checked_out_at: null, defaultTo: 'SEQ-3' },
}

const VOICE_SCRIPT = [
  'Move TS-1042 from Freezer B to Sequencing Station 3 for 2 PM.',
  'Move EV-2201 to the Forensics Lab now.',
  'Move TS-1077 from Freezer A to Sequencing Station 3.',
  'Move TS-1024 from Freezer B to Sequencing Station 3.', // deliberately unknown → clarification flow
]

// ---------- state ----------
let S
function fresh() {
  if (S) for (const r of S.runs) r.dead = true
  S = {
    seq: 0,
    ledger: [],
    scans: [],
    transfers: {},
    approvals: {},
    requests: {},
    runs: [],
    grants: { 'GRANT-0007': { _id: 'GRANT-0007', grantor: 'user:jlee', grantee: 'agent:custody-01', status: 'active', expires_at: new Date(Date.now() + 6 * 3600e3).toISOString() } },
    samples: structuredClone(SAMPLES_SEED),
    tr: 30,
    vr: 18,
    apr: 11,
    corr: 0,
    voiceCalls: 0,
  }
}
fresh()

const clients = new Set()
function broadcast(kind, data) {
  const msg = `data: ${JSON.stringify({ kind, data })}\n\n`
  for (const c of clients) c.write(msg)
}
function emitLedger(e) {
  S.ledger.push(e)
  broadcast('ledger', e)
}
function setTransfer(t, patch) {
  Object.assign(t, patch, { updated_at: now() })
  broadcast('transfer', { ...t })
}

// ---------- ledger helpers ----------
function led(ctx, o) {
  const t = ctx.t
  const e = {
    _id: `L-${String(++S.seq).padStart(5, '0')}`,
    timestamp: now(),
    entry_type: 'decision',
    transfer_id: t._id,
    sample_id: t.sample_id,
    request_id: t.request_id,
    correlation_id: `c-${String(++S.corr).padStart(4, '0')}`,
    actor: 'agent:custody-01',
    on_behalf_of: t.requested_by,
    grant_id: t.grant_id,
    policy_id: 'custody-default',
    policy_version: 3,
    decision: 'allow',
    rule_hits: [],
    status: null,
    ...o,
  }
  emitLedger(e)
  return e
}

function emitScan(ctx, type, locationId, corr, from, to) {
  const s = {
    _id: `SC-${String(++S.seq).padStart(5, '0')}`,
    scanner_id: `scanner:reader-${locationId}`,
    type,
    sample_id: ctx.t.sample_id,
    location_id: locationId,
    from_custodian: from,
    to_custodian: to,
    correlation_id: corr,
    scanned_at: now(),
    signature: 'hmac-sha256:mock' + Math.random().toString(16).slice(2, 18),
  }
  S.scans.push(s)
  broadcast('scan', s)
  return s
}

// ---------- run context ----------
class Halt extends Error {}

function makeCtx(t) {
  const ctx = {
    t,
    dead: false,
    halted: false,
    haltInfo: null,
    done: [], // completed reversible tools, for compensation
    checkedOut: false,
    delivered: false,
    pos: null,
    waiters: {},
  }
  ctx.haltP = new Promise((res) => (ctx.triggerHalt = res))
  // Sleep that aborts when a halt/stop arrives. Throws Halt.
  ctx.step = async (ms) => {
    await Promise.race([sleep(ms), ctx.haltP])
    if (ctx.dead) throw new Error('reset')
    if (ctx.halted) throw new Halt()
  }
  ctx.until = async (promise) => {
    await Promise.race([promise, ctx.haltP])
    if (ctx.dead) throw new Error('reset')
    if (ctx.halted) throw new Halt()
  }
  S.runs.push(ctx)
  return ctx
}

// Right-angle routes (room -> corridor lane -> room), matching the dashboard map.
const elbow = (a, b, c) => [a, { x: a.x, y: c.y }, { x: b.x, y: c.y }, b].filter((p, i, arr) => i === 0 || Math.hypot(p.x - arr[i - 1].x, p.y - arr[i - 1].y) > 0.5)
const route = (t) => elbow(loc(t.from).map, loc(t.to).map, loc('CORR-1').map)
function recoveryPath(pos, back) {
  const c = loc('CORR-1').map
  const pts = [pos]
  if (Math.abs(pos.y - c.y) > 1) pts.push({ x: pos.x, y: c.y })
  pts.push({ x: back.x, y: c.y }, back)
  return pts.filter((p, i, arr) => i === 0 || Math.hypot(p.x - arr[i - 1].x, p.y - arr[i - 1].y) > 0.5)
}
const lerp = (a, b, k) => ({ x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k })

// Moves the courier along points, emitting courier_position ticks (in memory/SSE only).
// totalMs is spread across segments in proportion to their length.
async function travel(ctx, points, totalMs, interruptible) {
  const lens = points.slice(1).map((p, i) => Math.hypot(p.x - points[i].x, p.y - points[i].y))
  const sum = lens.reduce((x, y) => x + y, 0) || 1
  let covered = 0
  for (let i = 0; i < lens.length; i++) {
    const ticks = Math.max(1, Math.round(((lens[i] / sum) * totalMs) / 400))
    for (let k = 1; k <= ticks; k++) {
      if (interruptible) await ctx.step(400)
      else await sleep(400)
      const p = lerp(points[i], points[i + 1], k / ticks)
      ctx.pos = p
      broadcast('courier_position', { transfer_id: ctx.t._id, x: p.x, y: p.y, progress: (covered + lens[i] * (k / ticks)) / sum })
    }
    covered += lens[i]
  }
}

// Gate-shaped tool call: decision first, then outcome, then (if physical) scan + verification.
async function tool(ctx, name, { reversible, reason, args = {}, scan, summary }) {
  const d = led(ctx, { tool: name, reversible, reason, args, summary: `Agent ${summary} (policy v3).` })
  await ctx.step(450)
  if (!scan) {
    led(ctx, { entry_type: 'outcome', correlation_id: d.correlation_id, tool: name, reversible, status: 'confirmed', summary: `${summary.replace(/^requested |^reserved |^looked up /, '')}: service confirmed.`, refs: [d._id] })
    if (reversible) ctx.done.push(name)
    return d
  }
  led(ctx, { entry_type: 'outcome', correlation_id: d.correlation_id, tool: name, reversible, status: 'sent', summary: `Lab service accepted ${name.replace(/_/g, ' ')}; waiting for the scanner.`, refs: [d._id] })
  await ctx.step(1100)
  emitScan(ctx, scan.type, scan.at, d.correlation_id, scan.from, scan.to)
  led(ctx, { entry_type: 'verification', correlation_id: d.correlation_id, tool: name, reversible, status: 'confirmed', summary: `Scanner reader-${scan.at} confirmed ${scan.type} of ${ctx.t.sample_id}.`, refs: [d._id] })
  return d
}

// ---------- the agent run ----------
async function run(ctx) {
  const t = ctx.t
  const sample = S.samples[t.sample_id]
  const from = loc(t.from).name
  const to = loc(t.to).name
  try {
    if (S.grants[t.grant_id]?.status !== 'active') {
      led(ctx, { tool: 'lookup_sample', decision: 'deny', rule_hits: ['grant_not_active'], reason: 'The grant is not active.', summary: `Denied: ${t.grant_id} has been revoked. Nothing was sent to any lab system.` })
      setTransfer(t, { state: 'cancelled' })
      return
    }
    await ctx.step(600)
    await tool(ctx, 'lookup_sample', { reversible: null, reason: 'Resolve sample and current location.', args: { sample_id: t.sample_id }, summary: `looked up ${t.sample_id} for ${t.requested_by.replace('user:', '')}` })
    await tool(ctx, 'reserve_station', { reversible: true, reason: `Hold ${to} for the arrival window.`, args: { station_id: t.to }, summary: `reserved ${t.to}` })
    await tool(ctx, 'request_courier', { reversible: true, reason: `Courier needed ${t.from} to ${t.to}.`, args: { from: t.from, to: t.to }, summary: 'requested a courier' })
    await tool(ctx, 'notify', { reversible: true, reason: 'Tell the receiving station to expect the sample.', args: { user_id: t.requested_by }, summary: 'notified the destination' })
    setTransfer(t, { state: 'reserved', reservation_id: 'RES-551', courier_job_id: 'CJ-208' })

    // Read-back gate (irreversible step ahead).
    led(ctx, { tool: 'checkout_sample', reversible: false, decision: 'pending_confirmation', rule_hits: ['readback_required'], summary: 'Checkout is waiting for the technician to confirm the read-back.', reason: 'Voice/typed requests need a read-back before the first irreversible step.' })
    setTransfer(t, { state: 'blocked', blocked_reason: 'readback_pending' })
    broadcast('confirmation_request', {
      transfer_id: t._id,
      sample_id: t.sample_id,
      from: t.from,
      to: t.to,
      readback_text: `I'll check out ${t.sample_id} from ${from} and send it to ${to}, arriving about 2:04. Confirm?`,
      requested_arrival: '2:00 PM',
      estimated_arrival: '2:04 PM',
    })
    await ctx.until(new Promise((res) => (ctx.waiters.confirm = res)))
    led(ctx, { entry_type: 'confirmation', tool: null, summary: `${t.requested_by.replace('user:', '')} confirmed the read-back by button.`, response: { readback_text: `Check out ${t.sample_id} to ${to}`, confirmed_by: t.requested_by, method: 'button' } })
    setTransfer(t, { state: 'reserved', blocked_reason: null, readback_confirmed: true })

    // Forensic gate: supervisor approval.
    if (sample.restriction === 'forensic') {
      led(ctx, { tool: 'checkout_sample', reversible: false, decision: 'pending_approval', rule_hits: ['forensic_requires_supervisor'], summary: `Checkout of ${t.sample_id} blocked: forensic sample needs supervisor approval. The freezer service was not called.`, reason: 'Forensic evidence moves require a supervisor.' })
      const apr = {
        _id: `APR-${String(++S.apr).padStart(4, '0')}`,
        transfer_id: t._id,
        sample_id: t.sample_id,
        requested_for_tool: 'checkout_sample',
        reason_requested: 'forensic_requires_supervisor',
        approver_role: 'supervisor',
        requested_by: t.requested_by,
        status: 'pending',
        decided_by: null,
        expires_at: new Date(Date.now() + 15 * 60e3).toISOString(),
      }
      S.approvals[apr._id] = apr
      ctx.approvalId = apr._id
      broadcast('approval', { ...apr })
      setTransfer(t, { state: 'blocked', blocked_reason: 'approval_pending', approval_id: apr._id })
      await ctx.until(new Promise((res) => (ctx.waiters.approval = res)))
      if (S.approvals[apr._id].status === 'denied') {
        led(ctx, { tool: 'checkout_sample', reversible: false, decision: 'deny', summary: 'Supervisor denied the move. Cancelling.' })
        throw new Halt()
      }
      setTransfer(t, { state: 'reserved', blocked_reason: null })
    }

    await tool(ctx, 'checkout_sample', { reversible: false, reason: `${t.to} reserved and courier assigned; ETA within ${sample.max_minutes_out} min limit.`, args: { sample_id: t.sample_id }, summary: `checked out ${t.sample_id} from ${t.from}`, scan: { type: 'checkout', at: t.from, from: `location:${t.from}`, to: `location:${t.from}` } })
    ctx.checkedOut = true
    sample.status = 'out'
    sample.checked_out_at = now()
    setTransfer(t, { state: 'checked_out' })

    await tool(ctx, 'handoff_to_courier', { reversible: false, reason: 'Courier on site; hand the sample over.', args: { transfer_id: t._id }, summary: `handed ${t.sample_id} to the courier`, scan: { type: 'handoff', at: t.from, from: `location:${t.from}`, to: 'courier:CJ-208' } })
    setTransfer(t, { state: 'in_transit' })
    await travel(ctx, route(t), 12000, true)

    await tool(ctx, 'deliver', { reversible: false, reason: 'Courier arrived at the destination.', args: { transfer_id: t._id }, summary: `delivered ${t.sample_id} to ${t.to}`, scan: { type: 'intake', at: t.to, from: 'courier:CJ-208', to: `location:${t.to}` } })
    ctx.delivered = true
    sample.status = 'stored'
    sample.location_id = t.to
    sample.checked_out_at = null
    setTransfer(t, { state: 'received' })
  } catch (e) {
    if (e instanceof Halt) await recover(ctx)
    else if (!ctx.dead) console.error(e)
  }
}

// Rollback for reversible actions, recovery for the irreversible one.
async function recover(ctx) {
  const t = ctx.t
  const sample = S.samples[t.sample_id]
  const by = ctx.haltInfo?.by ?? t.requested_by
  const why = ctx.haltInfo?.reason ?? 'denied'
  const rec = (o) => led(ctx, { policy_id: 'recovery', policy_version: 1, actor: 'system:recovery', on_behalf_of: by, decision: 'allow', ...o })

  if (ctx.approvalId && S.approvals[ctx.approvalId].status === 'pending') {
    S.approvals[ctx.approvalId] = { ...S.approvals[ctx.approvalId], status: 'denied', decided_by: 'system:halt' }
    broadcast('approval', { ...S.approvals[ctx.approvalId] })
  }
  if (why !== 'denied') {
    led(ctx, { decision: 'halt', tool: null, rule_hits: [why], summary: `${why === 'grant revoked' ? `Grant ${t.grant_id} revoked` : 'Stop pressed'} by ${by.replace('user:', '')}. Agent halted.`, reason: why })
    setTransfer(t, { state: 'revoked', halt: true, blocked_reason: null })
    await sleep(900)
  }

  const comp = { notify: 'Retracted the destination notice.', request_courier: 'Cancelled courier job CJ-208.', reserve_station: `Cancelled reservation RES-551 at ${t.to}.` }
  for (const name of [...ctx.done].reverse()) {
    rec({ entry_type: 'compensation', tool: name === 'notify' ? 'notify' : name === 'request_courier' ? 'cancel_courier_job' : 'cancel_reservation', reversible: true, summary: comp[name] })
    await sleep(600)
  }

  if (!ctx.checkedOut || ctx.delivered) {
    setTransfer(t, { state: 'cancelled', halt: false })
    return
  }

  setTransfer(t, { state: 'recovering' })
  const back = t.from
  rec({ entry_type: 'recovery', tool: 'return_to_storage', reversible: false, args: { sample_id: t.sample_id, freezer_id: back }, response: { freezer_id: back }, summary: `Returning ${t.sample_id} to the nearest certified freezer (${loc(back).name}).` })
  const start = ctx.pos ?? loc(t.from).map
  await travel(ctx, recoveryPath(start, loc(back).map), 5000, false)
  const corr = `c-${String(++S.corr).padStart(4, '0')}`
  emitScan(ctx, 'checkin', back, corr, 'courier:CJ-208', `location:${back}`)
  rec({ entry_type: 'verification', correlation_id: corr, tool: 'return_to_storage', reversible: false, status: 'confirmed', summary: `Scanner confirmed check-in of ${t.sample_id} at ${back}.` })
  const minutes = (Date.now() - Date.parse(sample.checked_out_at)) / 60000
  sample.status = 'stored'
  sample.location_id = back
  sample.checked_out_at = null
  setTransfer(t, { state: 'recovered', halt: false })
  const exceeded = minutes > sample.max_minutes_out
  rec({ entry_type: 'exposure', tool: null, response: { minutes_out: Math.round(minutes * 100) / 100, exceeded }, summary: `${t.sample_id} was out ${minutes.toFixed(2)} min, ${exceeded ? 'exceeding' : 'within'} the ${sample.max_minutes_out} min limit.` })
}

// ---------- intake ----------
function parseText(text) {
  const id = /\b([A-Z]{2}-\d{4})\b/i.exec(text)?.[1]?.toUpperCase() ?? null
  const sample = id ? S.samples[id] : null
  const byName = (n) => LOCATIONS.find((l) => l.kind !== 'corridor' && l.name.toLowerCase() === n.toLowerCase())
  const fromMatch = /from\s+(freezer [ab])/i.exec(text)
  const from = fromMatch ? LOCATIONS.find((l) => l.name.toLowerCase() === fromMatch[1].toLowerCase())?._id : sample?.location_id
  const toMatch = /to\s+(sequencing station 3|forensics lab)/i.exec(text)
  const to = toMatch ? byName(toMatch[1])?._id : sample?.defaultTo
  return { id, sample, from: from ?? null, to: to ?? null }
}

function intake(text, userId, source) {
  const p = parseText(text)
  const reqId = `VR-${String(++S.vr).padStart(4, '0')}`
  const base = { _id: reqId, source, speaker_id: userId, transcript: text, audio_sha256: source === 'voice' ? 'mock-sha256-' + reqId : null, audio_verified: null, low_confidence_terms: [], created_at: now() }

  if (!p.sample) {
    S.requests[reqId] = { ...base, low_confidence_terms: p.id ? [p.id] : [] }
    return {
      request_id: reqId,
      transcript: text,
      parsed: { sample_id: null, from: p.from, to: p.to, window_start: null },
      low_confidence_terms: p.id ? [p.id] : [],
      transfer_id: null,
      alternatives: [{ field: 'sample_id', options: Object.keys(S.samples) }],
    }
  }

  const t = {
    _id: `TR-${String(++S.tr).padStart(4, '0')}`,
    sample_id: p.sample._id,
    from: p.from,
    to: p.to,
    requested_by: userId,
    grant_id: 'GRANT-0007',
    request_id: reqId,
    source,
    readback_confirmed: false,
    blocked_reason: null,
    state: 'requested',
    halt: false,
    updated_at: now(),
  }
  S.transfers[t._id] = t
  base.transfer_id = t._id
  base.parsed = { sample_id: t.sample_id, from: t.from, to: t.to }
  S.requests[reqId] = base
  const ctx = makeCtx(t)
  led(ctx, {
    entry_type: 'intake',
    actor: userId,
    tool: null,
    reversible: null,
    summary: `${userId.replace('user:', '')} requested by ${source}: '${text}'`,
    response: { transcript: text, audio_sha256: base.audio_sha256, model_id: source === 'voice' ? 'scribe_v3' : null },
  })
  broadcast('transfer', { ...t })
  void run(ctx)
  return { request_id: reqId, transcript: text, parsed: { sample_id: t.sample_id, from: t.from, to: t.to, window_start: null }, low_confidence_terms: [], transfer_id: t._id }
}

const active = () => S.runs.filter((r) => !r.dead && !['received', 'recovered', 'cancelled'].includes(r.t.state) && !r.halted)
function haltRuns(runs, by, reason) {
  for (const r of runs) {
    r.halted = true
    r.haltInfo = { by, reason }
    r.triggerHalt()
  }
}

// ---------- audit ----------
function audit(question) {
  const q = question.toLowerCase()
  const sample = /\b([A-Z]{2}-\d{4})\b/i.exec(question)?.[1]?.toUpperCase()
  const grant = /\b(GRANT-\d{4})\b/i.exec(question)?.[1]?.toUpperCase()
  const reqsFor = (entries) => [...new Set(entries.map((e) => e.request_id).filter(Boolean))].map((id) => S.requests[id]).filter(Boolean)
  const pack = (answer, entries, scans = S.scans, mode = 'canned') => ({ answer, mode, entries, scans, requests: reqsFor(entries) })

  if (q.includes('gap')) {
    const gaps = S.ledger.filter((e) => e.entry_type === 'outcome' && e.status === 'sent' && e.reversible === false && !S.scans.some((s) => s.correlation_id === e.correlation_id))
    return { ...pack(`${gaps.length} irreversible action(s) were sent without a matching scan.`, gaps), gaps }
  }
  if (q.includes('without') && (q.includes('supervisor') || q.includes('approval'))) {
    const rows = S.ledger.filter((e) => e.entry_type === 'decision' && e.decision === 'allow' && ['checkout_sample', 'handoff_to_courier', 'deliver'].includes(e.tool) && S.samples[e.sample_id]?.restriction === 'forensic' && !Object.values(S.approvals).some((a) => a.transfer_id === e.transfer_id && a.status === 'approved'))
    return pack(`${rows.length} restricted move(s) without a supervisor. ${rows.length === 0 ? 'The gate held.' : ''}`, rows)
  }
  if (grant) {
    const rows = S.ledger.filter((e) => e.grant_id === grant)
    return pack(`${rows.length} ledger entries under ${grant}.`, rows)
  }
  if (sample) {
    const rows = S.ledger.filter((e) => e.sample_id === sample)
    return pack(`${rows.length} ledger entries for ${sample}.`, rows, S.scans.filter((s) => s.sample_id === sample))
  }
  return pack('Try a sample ID (TS-1077), a grant ID, or "custody gaps".', [])
}

// ---------- http ----------
const send = (res, status, body) => {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(body === undefined ? '' : JSON.stringify(body))
}
const readJson = (req) =>
  new Promise((resolve) => {
    let raw = ''
    req.on('data', (c) => (raw += c))
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch {
        resolve({})
      }
    })
  })
const drain = (req) => new Promise((resolve) => { req.on('data', () => {}); req.on('end', resolve) })

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x')
  const path = url.pathname
  const m = req.method

  if (m === 'GET' && path === '/stream') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' })
    res.write(': mock stream connected\n\n')
    clients.add(res)
    const hb = setInterval(() => res.write(': hb\n\n'), 15000)
    req.on('close', () => {
      clearInterval(hb)
      clients.delete(res)
    })
    return
  }
  if (m === 'GET' && path === '/health') return send(res, 200, { status: 'ok', mock: true })
  if (m === 'GET' && path === '/state') {
    return send(res, 200, {
      transfers: Object.values(S.transfers),
      ledger: S.ledger,
      scans: S.scans,
      approvals: Object.values(S.approvals),
      samples: Object.values(S.samples).map(({ defaultTo: _d, ...s }) => s),
      locations: LOCATIONS,
      grants: Object.values(S.grants),
      demo_scripted: false,
      demo_speed: null,
    })
  }

  if (m === 'POST' && path === '/voice/requests') {
    await drain(req) // multipart audio is ignored; the transcript is scripted
    const text = VOICE_SCRIPT[S.voiceCalls++ % VOICE_SCRIPT.length]
    return send(res, 200, intake(text, 'user:jlee', 'voice'))
  }

  const body = m === 'POST' ? await readJson(req) : {}
  const user = body.user_id ?? 'user:jlee'

  if (m === 'POST' && path === '/transfers') {
    if (!body.text) return send(res, 422, { detail: 'text is required' })
    return send(res, 200, intake(String(body.text), user, 'typed'))
  }

  let r
  if (m === 'POST' && (r = /^\/transfers\/([^/]+)\/confirm$/.exec(path))) {
    const ctx = S.runs.find((x) => x.t._id === r[1])
    if (!ctx) return send(res, 404, { detail: `unknown transfer ${r[1]}` })
    if (ctx.halted || ['revoked', 'recovering', 'recovered', 'cancelled', 'received'].includes(ctx.t.state)) {
      return send(res, 409, { detail: `This transfer was halted (${ctx.haltInfo?.reason ?? 'no longer active'}), so it can no longer be confirmed.` })
    }
    if (ctx.t.blocked_reason !== 'readback_pending') return send(res, 409, { detail: 'No read-back is pending for this transfer.' })
    ctx.waiters.confirm?.()
    return send(res, 204)
  }

  if (m === 'POST' && (r = /^\/approvals\/([^/]+)$/.exec(path))) {
    const apr = S.approvals[r[1]]
    if (!apr) return send(res, 404, { detail: `unknown approval ${r[1]}` })
    if (user !== 'user:mokafor') return send(res, 403, { detail: 'only a supervisor can decide this approval' })
    if (apr.status !== 'pending') return send(res, 409, { detail: `approval already ${apr.status}` })
    apr.status = body.decision === 'approved' ? 'approved' : 'denied'
    apr.decided_by = user
    broadcast('approval', { ...apr })
    S.runs.find((x) => x.t._id === apr.transfer_id)?.waiters.approval?.()
    return send(res, 204)
  }

  if (m === 'POST' && path === '/control/stop') {
    haltRuns(active(), user, 'stop pressed')
    return send(res, 204)
  }

  if (m === 'POST' && (r = /^\/grants\/([^/]+)\/revoke$/.exec(path))) {
    const g = S.grants[r[1]]
    if (!g) return send(res, 404, { detail: `unknown grant ${r[1]}` })
    g.status = 'revoked'
    haltRuns(active().filter((x) => x.t.grant_id === g._id), user, 'grant revoked')
    return send(res, 204)
  }

  if (m === 'POST' && path === '/audit/query') return send(res, 200, audit(String(body.question ?? '')))

  if (m === 'POST' && path === '/mock/reset') {
    fresh()
    return send(res, 200, { reset: true })
  }

  send(res, 404, { detail: `mock: ${m} ${path} is not implemented` })
})

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`Port ${PORT} is already in use, so another server (often a mock started earlier) is running there.`)
    console.error('You can use it as is, or stop it first:')
    console.error(`  PowerShell: Get-NetTCPConnection -LocalPort ${PORT} -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`)
    console.error(`  or run this one on another port: PORT=8001 node mock-data/server.mjs (then point the Vite proxy at it)`)
    process.exit(1)
  }
  throw err
})

server.listen(PORT, () => {
  console.log(`CustodyOS MOCK agent service on http://localhost:${PORT}  (speed x${SPEED})`)
  console.log('This is simulated data. Start the dashboard with: cd dashboard && npm run dev')
})

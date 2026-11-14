// The ONLY module that talks to the agent service. The browser never contacts
// MongoDB or ElevenLabs. Base URL defaults to /api (Vite proxies it to :8000 and
// strips the prefix, so /api/voice/requests → FastAPI POST /voice/requests).
import type { AudioVerification, AuditResult, IntakeResponse, ReissueResult, Snapshot } from './types'

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '/api'

export type ApiErrorKind = 'network' | 'http' | 'invalid' | 'not_implemented'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  constructor(kind: ApiErrorKind, message: string, status: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, init)
  } catch {
    throw new ApiError('network', 'Cannot reach the agent service. Is it running on :8000?')
  }
  if (!res.ok) {
    let detail = ''
    try {
      const body: unknown = await res.json()
      if (typeof body === 'object' && body !== null && 'detail' in body) {
        const d = (body as { detail: unknown }).detail
        detail = typeof d === 'string' ? d : JSON.stringify(d)
      }
    } catch {
      /* body was not JSON */
    }
    if (res.status === 404 || res.status === 501) {
      throw new ApiError('not_implemented', detail || `${path} is not available on the backend yet`, res.status)
    }
    throw new ApiError('http', detail || `${init?.method ?? 'GET'} ${path} failed (${res.status})`, res.status)
  }
  if (res.status === 204) return undefined as T
  try {
    return (await res.json()) as T
  } catch {
    throw new ApiError('invalid', `${path} returned a response that is not valid JSON`, res.status)
  }
}

const post = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

function assertIntake(v: unknown): IntakeResponse {
  if (typeof v !== 'object' || v === null) throw new ApiError('invalid', 'Intake response was not an object')
  const r = v as Partial<IntakeResponse>
  if (typeof r.request_id !== 'string' || typeof r.transcript !== 'string' || typeof r.parsed !== 'object' || r.parsed === null) {
    throw new ApiError('invalid', 'Intake response is missing request_id, transcript or parsed')
  }
  return { ...(r as IntakeResponse), low_confidence_terms: r.low_confidence_terms ?? [], transfer_id: r.transfer_id ?? null }
}

// ---- documented endpoints (ARCHITECTURE.md "API for dashboard") ----

/** Spoken request. `user_id` is the logged-in identity, never inferred from the audio. */
export async function sendVoiceRequest(audio: Blob, userId: string): Promise<IntakeResponse> {
  const form = new FormData()
  form.append('audio', audio, 'request.webm')
  form.append('user_id', userId)
  return assertIntake(await request<unknown>('/voice/requests', { method: 'POST', body: form }))
}

/** Typed fallback. Same downstream path and gate as voice; response has the same shape. */
export async function sendTypedRequest(text: string, userId: string): Promise<IntakeResponse> {
  return assertIntake(await request<unknown>('/transfers', post({ text, user_id: userId })))
}

export const confirmTransfer = (transferId: string, userId: string, method: 'button' | 'voice' = 'button') =>
  request<void>(`/transfers/${encodeURIComponent(transferId)}/confirm`, post({ user_id: userId, method }))

export const decideApproval = (approvalId: string, userId: string, decision: 'approved' | 'denied') =>
  request<void>(`/approvals/${encodeURIComponent(approvalId)}`, post({ user_id: userId, decision }))

export const stopAgent = (userId: string, transferId: string | null = null) =>
  request<void>('/control/stop', post({ user_id: userId, scope: transferId ? 'transfer' : 'all', transfer_id: transferId }))

export const revokeGrant = (grantId: string, userId: string) =>
  request<void>(`/grants/${encodeURIComponent(grantId)}/revoke`, post({ user_id: userId }))

/**
 * Ask the backend to issue a NEW grant to replace a revoked one. The server enforces who may do this
 * (a supervisor), what the new grant may contain (never wider than the old) and how long it lasts.
 * The client sends only who is asking and why; it can never choose scope or expiry.
 */
export const reissueGrant = (revokedGrantId: string, by: string, reason: string) =>
  request<ReissueResult>(`/grants/${encodeURIComponent(revokedGrantId)}/reissue`, post({ by, reason }))

export const auditQuery = (question: string, userId: string) =>
  request<AuditResult>('/audit/query', post({ question, user_id: userId })).then((r) => ({
    ...r,
    entries: r.entries ?? [],
  }))

/** Every ledger row for a transfer, each joined to the original request (`request`). */
export const auditTransfer = (transferId: string) => request<unknown[]>(`/audit/transfer/${encodeURIComponent(transferId)}`)

/** Re-hashes the stored audio and compares it with the hash recorded at intake. Done by the backend, not here. */
export const verifyAudio = (requestId: string) => request<AudioVerification>(`/audit/requests/${encodeURIComponent(requestId)}/verify`)

export const voiceAudioUrl = (requestId: string): string =>
  `${API_BASE}/voice/requests/${encodeURIComponent(requestId)}/audio`

export const streamUrl = (): string => `${API_BASE}/stream`

// ---- adapters for endpoints the docs imply but do not specify yet ----
// These resolve to null/false instead of throwing when the backend lacks them.

/** Expected: GET /state → Snapshot. Used to re-sync after an SSE reconnect. */
export async function getSnapshot(): Promise<Snapshot | null> {
  try {
    return await request<Snapshot>('/state')
  } catch (e) {
    if (e instanceof ApiError && e.kind === 'not_implemented') return null
    throw e
  }
}

/**
 * GET /health. The backend answers 200 even when MongoDB is down, with `{status: "degraded", mongo: "down"}`
 * so the dashboard can show which part is unhealthy. A bare 200 therefore does not mean healthy.
 */
export async function checkHealth(): Promise<'up' | 'degraded' | 'down' | 'unknown'> {
  try {
    const h = await request<{ status?: string } | undefined>('/health')
    return h && typeof h === 'object' && typeof h.status === 'string' && h.status !== 'ok' ? 'degraded' : 'up'
  } catch (e) {
    if (e instanceof ApiError && e.kind === 'not_implemented') return 'unknown'
    return 'down'
  }
}

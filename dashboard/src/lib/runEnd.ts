// A run that ENDED abnormally is not the same thing as the gate saying no (GUARDRAILS_SPEC.md).
// The agent loop records it as a `violation` ledger row: actor `system:guardrail`, policy `guardrails`,
// decision `halt`, and `rule_hits` = one of the four statuses below. People need to tell
// "the agent was blocked" (a policy decision) from "the agent broke or ran out of budget" (this).
import type { RuleExplanation } from './rules'
import type { LedgerEntry } from './types'

export const RUN_END_STATUSES = ['budget', 'llm_error', 'error', 'max_steps'] as const
export type RunEndStatus = (typeof RUN_END_STATUSES)[number]

const AFTER = 'Reversible steps are rolled back, and a sample that already left storage is returned and checked in by a scanner. Nothing is left half-done. You can send a new request.'

const RUN_END: Record<RunEndStatus, RuleExplanation> = {
  budget: {
    title: 'The agent used up its limit',
    why: 'It reached its cap on calls, or kept repeating a refused step, so the run was ended instead of letting it loop.',
    next: AFTER,
    kind: 'failure',
  },
  llm_error: {
    title: 'The AI model failed',
    why: 'The model was unavailable, timed out, or sent back something unusable, so the run could not continue.',
    next: AFTER + ' If it keeps happening, the model service may be down.',
    kind: 'failure',
  },
  error: {
    title: 'The agent hit an unexpected error',
    why: 'Something went wrong inside the agent, so the run was stopped safely.',
    next: AFTER + ' If it repeats, tell the team: the reason is in the ledger entry.',
    kind: 'failure',
  },
  max_steps: {
    title: 'The agent ran out of steps',
    why: 'It could not reach an answer within its step limit, so the run was ended.',
    next: AFTER,
    kind: 'failure',
  },
}

export function explainRunEnd(status: string): RuleExplanation {
  return (
    RUN_END[status as RunEndStatus] ?? {
      title: 'The agent stopped early',
      why: 'The run ended before it finished.',
      next: AFTER,
      kind: 'failure',
    }
  )
}

/** The ledger row written when a run ends abnormally. */
export function isRunEnd(e: Pick<LedgerEntry, 'entry_type' | 'actor' | 'rule_hits'>): boolean {
  return e.entry_type === 'violation' && e.actor === 'system:guardrail' && (e.rule_hits ?? []).some((r) => (RUN_END_STATUSES as readonly string[]).includes(r))
}

export const runEndStatus = (e: Pick<LedgerEntry, 'rule_hits'>): string => (e.rule_hits ?? []).find((r) => (RUN_END_STATUSES as readonly string[]).includes(r)) ?? 'error'

/** The most recent abnormal ending for a transfer, if any. */
export function runEndFor(ledger: LedgerEntry[], transferId: string | null | undefined): LedgerEntry | null {
  if (!transferId) return null
  return [...ledger].reverse().find((e) => e.transfer_id === transferId && isRunEnd(e)) ?? null
}

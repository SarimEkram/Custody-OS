// Plain-language explanations for the policy gate's rule hits (agent/policy_gate.py, agent/watchers.py).
// The gate decides; this only translates its reason into something a person can act on.

export interface RuleExplanation {
  title: string
  why: string
  next: string
  /** policy: the gate applied a rule. guardrail: a safety check refused the agent's own call. failure: the run broke. */
  kind?: 'policy' | 'guardrail' | 'failure'
}

/** Rules that mean "the agent has no valid authority". Used to tell a revoked grant from an ordinary denial. */
export const GRANT_RULES: readonly string[] = ['grant_revoked', 'grant_expired', 'grant_missing']

/** A supervisor issued a replacement grant. Appears in `rule_hits` of the ledger entry that records it. */
export const GRANT_REISSUED = 'grant_reissued'

const RESTORE = 'A supervisor can issue a new grant (Restore authority). The revoked grant stays revoked on record.'

const RULES: Record<string, RuleExplanation> = {
  grant_revoked: {
    title: 'The agent’s authority was revoked',
    why: 'The grant that lets the agent act on your behalf has been revoked, so the gate refuses every new action.',
    next: RESTORE,
  },
  grant_expired: {
    title: 'The agent’s authority expired',
    why: 'The time-limited grant that lets the agent act has run out.',
    next: RESTORE,
  },
  grant_missing: {
    title: 'The agent has no grant',
    why: 'No delegation from a person was found, or it was removed.',
    next: RESTORE,
  },
  grant_wrong_grantor: {
    title: 'The grant belongs to someone else',
    why: 'The grant on file was issued by a different person than the one making this request.',
    next: 'Use the identity that issued the grant, or have a new one issued.',
  },
  grant_wrong_grantee: {
    title: 'The grant is for a different agent',
    why: 'The grant on file is delegated to another agent.',
    next: 'Have a grant issued to this agent.',
  },
  action_out_of_scope: {
    title: 'The grant doesn’t allow this action',
    why: 'This step is outside what the grant lets the agent do.',
    next: 'Ask the grantor for a grant that includes it.',
  },
  sample_type_out_of_scope: {
    title: 'The grant doesn’t cover this kind of sample',
    why: 'The sample’s type is outside the grant’s scope.',
    next: 'Ask the grantor for a grant that covers it.',
  },
  location_out_of_scope: {
    title: 'A location is outside the grant',
    why: 'The source or destination isn’t one the grant allows.',
    next: 'Choose locations the grant covers, or have it widened.',
  },
  restriction_above_grant: {
    title: 'The sample is more restricted than the grant allows',
    why: 'The sample’s restriction level is higher than the grant’s maximum.',
    next: 'A grant with a higher clearance is needed.',
  },
  window_after_grant_expiry: {
    title: 'The requested time is after the grant ends',
    why: 'The move is scheduled for a time the grant no longer covers.',
    next: 'Pick an earlier time, or have the grant extended.',
  },
  supervisor_denied: {
    title: 'A supervisor denied this transfer',
    why: 'The supervisor reviewed the request and said no.',
    next: 'Talk to the supervisor before trying again.',
  },
  destination_not_certified: {
    title: 'The destination isn’t certified for this sample',
    why: 'This sample needs a certification the destination doesn’t have.',
    next: 'Choose a certified location. Evidence needs a FORENSIC-certified one.',
  },
  not_in_recovery_allowlist: {
    title: 'Not allowed during recovery',
    why: 'Only a short list of safe actions can run while recovering a sample.',
    next: 'Wait for recovery to finish.',
  },
  // ---- guardrails (GUARDRAILS_SPEC.md): safety checks on the agent's own calls. Nothing runs when one refuses. ----
  unknown_tool: {
    title: 'The agent asked for a tool that doesn’t exist',
    why: 'The assistant named a tool outside the approved list, so the call was refused before anything ran.',
    next: 'Nothing was changed. Send the request again. If it keeps happening, tell the team.',
    kind: 'guardrail',
  },
  invalid_arguments: {
    title: 'The agent sent an incomplete or invalid instruction',
    why: 'A step had missing or malformed details (for example free text where an ID belongs), so it was refused.',
    next: 'Nothing was changed. Send the request again, or rephrase it.',
    kind: 'guardrail',
  },
  args_not_bound_to_transfer: {
    title: 'The agent tried to act on something you didn’t confirm',
    why: 'A step named a different sample, route or transfer than the plan you confirmed, so it was refused. The agent can only run the plan you approved.',
    next: 'Nothing was changed for that step. If this was unexpected, check the read-back and the activity list.',
    kind: 'guardrail',
  },
  duplicate_irreversible_step: {
    title: 'That step is already done or awaiting its scan',
    why: 'The agent tried to repeat a step that can’t be undone (such as checking the sample out) before the first attempt was confirmed by a scanner.',
    next: 'Wait for the scanner to confirm the first attempt. Nothing was done twice.',
    kind: 'guardrail',
  },
  gate_unavailable: {
    title: 'The safety check couldn’t run',
    why: 'The system couldn’t load what it needed to decide, so it refused rather than guess (it fails closed).',
    next: 'Nothing was changed. Try again shortly. If it persists, the database or policy may be unavailable.',
    kind: 'guardrail',
  },
  stop_switch: {
    title: 'Stopped with the Stop switch',
    why: 'Someone pressed Stop, which halts the agent’s active transfers.',
    next: 'Send a new request when you are ready to continue.',
  },
}

export function explainRule(rule: string): RuleExplanation {
  if (RULES[rule]) return RULES[rule]
  if (rule.startsWith('precondition:')) {
    const step = rule.slice('precondition:'.length).replace(/_/g, ' ')
    return {
      title: 'A required step isn’t done yet',
      why: `The gate needs “${step}” before it allows this action.`,
      next: 'Let the earlier steps finish, then try again.',
    }
  }
  const pretty = rule.replace(/[_:]/g, ' ')
  return { title: `Refused by policy (${pretty})`, why: 'The policy gate refused this step.', next: 'Open the ledger entry to see the details.' }
}

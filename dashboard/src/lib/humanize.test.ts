import { describe, expect, it } from 'vitest'
import { humanizeText } from './format'

describe('humanizeText (display-only)', () => {
  it('replaces the tool name at the start of a gate sentence', () => {
    expect(humanizeText('Agent checkout_sample TS-1042 for jlee under GRANT-0007 (policy custody-default v3): ALLOWED.'))
      .toBe('Check out sample TS-1042 for jlee under GRANT-0007 (policy custody-default v3): ALLOWED.')
    expect(humanizeText('Agent notify TS-1042 for jlee: ALLOWED.')).toBe('Notify destination TS-1042 for jlee: ALLOWED.')
  })
  it('quotes the label inside other sentences', () => {
    expect(humanizeText('freezer accepted checkout_sample for TR-0001; awaiting checkout scan.'))
      .toBe('freezer accepted ‘Check out sample’ for TR-0001; awaiting checkout scan.')
    expect(humanizeText('Supervisor mokafor approved checkout_sample of EV-2201 for TR-0002'))
      .toBe('Supervisor mokafor approved ‘Check out sample’ of EV-2201 for TR-0002')
    expect(humanizeText('reserve_station call failed for TR-1: timeout'))
      .toBe('‘Reserve station’ call failed for TR-1: timeout')
  })
  it('leaves ordinary English alone', () => {
    const t = 'The courier will deliver the box; please notify the lab.'
    expect(humanizeText(t)).toBe(t)
    expect(humanizeText('Agent halted on TR-0001 (TS-1042, was in_transit): grant revoked.'))
      .toBe('Agent halted on TR-0001 (TS-1042, was in_transit): grant revoked.')
  })
  it('handles empty input', () => {
    expect(humanizeText(undefined)).toBe('')
    expect(humanizeText(null)).toBe('')
    expect(humanizeText('')).toBe('')
  })
})

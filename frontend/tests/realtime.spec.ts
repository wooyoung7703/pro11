import { describe, it, expect, beforeAll, vi } from 'vitest'
import { ageSec, computeConnStatus, connTooltip, SIG_FRESH_MS, RISK_FRESH_MS, SEVERE_LAG_MULTIPLIER } from '../src/utils/realtime'

// Freeze time to a fixed point for deterministic tests
const NOW = 1_700_000_000_000

describe('realtime utils', () => {
  beforeAll(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })

  it('ageSec returns null on invalid ts and seconds on valid ts', () => {
    expect(ageSec(null)).toBeNull()
    expect(ageSec(undefined)).toBeNull()
    expect(ageSec(NaN as unknown as number)).toBeNull()
    expect(ageSec(NOW - 5_000)).toBe(5)
  })

  it('computeConnStatus classifies based on base state and age', () => {
    // online -> ok
    expect(computeConnStatus('online', NOW, SIG_FRESH_MS)).toBe('ok')
    // offline/idle -> idle
    expect(computeConnStatus('offline', NOW, SIG_FRESH_MS)).toBe('idle')
    expect(computeConnStatus('idle', NOW, SIG_FRESH_MS)).toBe('idle')
    // lagging under severe threshold -> warning
    const freshSec = Math.floor(SIG_FRESH_MS / 1000)
    const justLagTs = NOW - (freshSec + 1) * 1000
    expect(computeConnStatus('lagging', justLagTs, SIG_FRESH_MS, SEVERE_LAG_MULTIPLIER)).toBe('warning')
    // lagging over severe threshold -> danger
    const severeSec = freshSec * SEVERE_LAG_MULTIPLIER
    const severeTs = NOW - (severeSec + 1) * 1000
    expect(computeConnStatus('lagging', severeTs, SIG_FRESH_MS, SEVERE_LAG_MULTIPLIER)).toBe('danger')
    // unknown -> neutral
    expect(computeConnStatus('something', NOW, SIG_FRESH_MS)).toBe('neutral')
  })

  it('connTooltip indicates lagging and severe lag', () => {
    const st = 'online'
    // No server_time yet
    expect(connTooltip({ status: st, lastTsMs: null, freshMs: SIG_FRESH_MS })).toMatch(/no server_time yet/)
    // Fresh
    expect(connTooltip({ status: st, lastTsMs: NOW - 1_000, freshMs: SIG_FRESH_MS })).not.toMatch(/lag/)
    // Lagging
    const freshSec = Math.floor(RISK_FRESH_MS / 1000)
    const lagTs = NOW - (freshSec + 2) * 1000
    expect(connTooltip({ status: st, lastTsMs: lagTs, freshMs: RISK_FRESH_MS })).toMatch(/lagging/)
    // Severe
    const severeSec = freshSec * SEVERE_LAG_MULTIPLIER
    const severeTs = NOW - (severeSec + 2) * 1000
    expect(connTooltip({ status: st, lastTsMs: severeTs, freshMs: RISK_FRESH_MS })).toMatch(/severe lag/)
  })
})

import type { CoreStatus } from './status';

// Centralized SSE freshness thresholds (ms)
// Allow env override via Vite
function numEnv(key: string, fallback: number): number {
  const raw = (import.meta as any).env?.[key];
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export const SIG_FRESH_MS = numEnv('VITE_SIG_FRESH_MS', 20_000); // signals fresh within 20s
export const RISK_FRESH_MS = numEnv('VITE_RISK_FRESH_MS', 15_000); // risk fresh within 15s
export const SEVERE_LAG_MULTIPLIER = numEnv('VITE_SEVERE_LAG_MULT', 3); // danger when age > fresh_ms * multiplier

export function ageSec(tsMs: number | null | undefined): number | null {
  if (!tsMs || !isFinite(tsMs)) return null;
  return Math.max(0, Math.floor((Date.now() - tsMs) / 1000));
}

/**
 * Classify UI status based on base connection state and last server time.
 * baseConn: 'online' | 'lagging' | 'offline' | 'idle' | other
 */
export function computeConnStatus(
  baseConn: string | null | undefined,
  lastTsMs: number | null | undefined,
  freshMs: number,
  severeMultiplier: number = SEVERE_LAG_MULTIPLIER,
): CoreStatus {
  const base = String(baseConn || 'unknown');
  if (base === 'online') return 'ok';
  if (base === 'offline' || base === 'idle') return 'idle';
  if (base === 'lagging') {
    const age = ageSec(lastTsMs) ?? 0;
    const severe = age > Math.floor((freshMs / 1000) * severeMultiplier);
    return severe ? 'danger' : 'warning';
  }
  return 'neutral';
}

/**
 * Standard tooltip formatter for SSE connection badges
 */
export function connTooltip(opts: {
  status: string | null | undefined,
  lastTsMs: number | null | undefined,
  freshMs: number,
}): string {
  const st = String(opts.status ?? 'unknown');
  const age = ageSec(opts.lastTsMs);
  if (age == null || !opts.lastTsMs) return `status: ${st} (no server_time yet)`;
  const t = new Date(opts.lastTsMs).toLocaleTimeString();
  const lagThreshold = Math.floor(opts.freshMs / 1000);
  const severeThreshold = lagThreshold * SEVERE_LAG_MULTIPLIER;
  const tag = age > severeThreshold ? ' • severe lag' : (age > lagThreshold ? ' • lagging' : '');
  return `status: ${st} • age: ${age}s • last: ${t}${tag}`;
}

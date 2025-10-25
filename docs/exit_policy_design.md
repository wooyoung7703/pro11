# Exit Policy Design (DB-managed)

This document defines a robust, DB-managed exit policy for bottom-entry strategies. It standardizes trailing stops, time stops, and partial exits with runtime-configurable settings and safe rollout.

## Goals

- Simple, reliable exits as the core (risk-first), with optional model overlays later.
- Centralized settings via DB-backed Admin Settings API (soft defaults; runtime apply).
- Backward-compatible with existing live_trailing_take_profit_pct.
- Strong validation, metrics, and easy rollback.

## High-level approach

- Core exits: ATR or Percent trailing stop, Time stop, Optional partial profit-taking, Cooldown.
- All configuration lives under namespaced keys `exit.*` in the settings store.
- Runtime changes apply without restart; soft defaults prevent 404 on first load.
- Feature flag `exit.enable_new_policy` for canary rollout and quick rollback.

## Settings (keys, types, defaults, ranges)

- exit.enable_new_policy: boolean (default false)
  - Turns on the new unified ExitPolicy. When false, legacy code path stays active.
- exit.trail.mode: string enum {"atr", "percent"} (default "atr")
- exit.trail.multiplier: number (default 2.0, range 0.5–4.0)
  - Used when mode="atr".
- exit.trail.percent: number (default 0.02 for 2.0%)
  - Used when mode="percent". Range 0.005–0.1 typical.
- exit.time_stop.bars: integer (default 8; range 1–60)
  - Max holding bars; 0 disables.
- exit.partial.enabled: boolean (default true)
- exit.partial.levels: array of objects (default [{"rr":1.0,"fraction":0.4}])
  - Each level: { rr: float (R multiple), fraction: float (0–1) }
  - Constraints: sum(fraction) ≤ 1; rr must be strictly increasing.
- exit.cooldown.bars: integer (default 3; range 0–100)
- exit.daily_loss_cap_r: number (default 4.0; range 0–20)
  - Optional strategy-level daily loss cap measured in R; 0 disables.
- exit.freeze_on_exit: boolean (default true)
  - Prevent immediate re-entry after a stop-out/exit for cooldown duration.

Backward compatibility:
- live_trailing_take_profit_pct (legacy) 
  - If present and non-null, map to `exit.trail.mode="percent"` and `exit.trail.percent`. 
  - Emit deprecation warning; server responses expose both for a transition period.

### Example GET (soft defaults provided)

```json
{
  "status": "ok",
  "item": {
    "exit.enable_new_policy": false,
    "exit.trail.mode": "atr",
    "exit.trail.multiplier": 2.0,
    "exit.trail.percent": 0.02,
    "exit.time_stop.bars": 8,
    "exit.partial.enabled": true,
    "exit.partial.levels": [{"rr": 1.0, "fraction": 0.4}],
    "exit.cooldown.bars": 3,
    "exit.daily_loss_cap_r": 4.0,
    "exit.freeze_on_exit": true
  }
}
```

### Example PUT (single key)

```json
{"value": {"mode": "atr", "multiplier": 2.5}}
```

Accepted shapes:
- Primitive: {"value": 0.02}
- Object for grouped keys: {"value": { ... }} when using a compound key endpoint.

## Algorithms

### ATR trailing stop

- Compute ATR with Wilder smoothing over window N (e.g., 14):

$$
\begin{aligned}
TR_t &= \max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|) \\
ATR_t &= ATR_{t-1} + \frac{TR_t - ATR_{t-1}}{N}
\end{aligned}
$$

- For long positions, update trailing stop each bar:

$$
TS_t = \max\big(TS_{t-1}, High_t - k \cdot ATR_t\big) \quad (k = \text{multiplier})
$$

- Exit when price crosses below TS_t. Handle NaN ATR by deferring trail until warm-up complete.

### Percent trailing stop

- Maintain highest_price_since_entry H_t; set:

$$
TS_t = \max(TS_{t-1}, H_t \cdot (1 - p)) \quad (p = \text{percent})
$$

### Time stop

- Exit if bars_since_entry ≥ `exit.time_stop.bars` (0 disables).

### Partial exits

- Define R: initial stop distance in price terms. 
- At level rr_i, when unrealized PnL ≥ rr_i × R, exit `fraction_i` of remaining size. Enforce rr strictly increasing; cap cumulative fraction ≤ 1.

### Cooldown

- After any exit, block re-entry for `exit.cooldown.bars`. If `exit.freeze_on_exit` true, also disallow scale-in during cooldown.

## Runtime apply

- Settings changes propagate to an in-memory ExitPolicy config immediately (no restart).
- Legacy key mapping applied on read; on write, prefer new keys and mirror percent to legacy for a transition period.

## API surface

- GET/PUT /admin/settings/<key>
- Optional bulk endpoints:
  - GET /admin/settings/exit
  - PUT /admin/settings/exit (object payload)

Responses wrapped with { status, item } as per existing conventions.

## Validation

- trail.mode in {"atr","percent"}
- trail.multiplier ∈ [0.5, 4.0]
- trail.percent ∈ (0, 0.5]
- time_stop.bars ∈ [0, 1000]
- partial.levels: array length ≤ 5; rr strictly increasing; 0< fraction ≤1; sum ≤1.
- cooldown.bars ∈ [0, 1000]
- daily_loss_cap_r ∈ [0, 100]

Return 400 with details when invalid.

## Metrics & logging

- Counters: exits_total{reason="trail|time|partial|risk_cap|manual"}
- Histograms: exit_rr, time_in_trade_bars, trail_gap_pct, slippage_bps
- Logs: structured JSON with exit_reason, prices, rr, fractions.

## Testing

- Unit: deterministic price streams to verify ATR/percent trails, time stop, partial schedules, cooldown.
- Endpoint: soft defaults for exit.* keys, GET/PUT happy and invalid paths.
- Backtest A/B: legacy vs new policy on same entries; report Sharpe/MDD/turnover/Win%.

## Rollout

- Default `exit.enable_new_policy=false`.
- Add Admin toggle; canary subset first.
- Mirror legacy percent key for a release; warn in logs when used.
- Quick rollback path: toggle flag off.

## Implementation sketch

- New: backend/apps/trading/service/exit_policy.py (pure functions + small state helpers)
- Integrate: auto_inference_loop uses ExitPolicy decisions; remove duplicated logic.
- Settings: extend runtime apply mapper in backend/apps/api/main.py; soft defaults provided; deprecate legacy key.
- Frontend: Admin Exit tab bound to keys with validation.

## Try it (examples)

- GET /admin/settings/exit → returns defaults if undefined.
- PUT /admin/settings/exit.trail.mode {"value":"atr"}
- PUT /admin/settings/exit.partial.levels {"value":[{"rr":1.0,"fraction":0.4},{"rr":2.0,"fraction":0.3}]}


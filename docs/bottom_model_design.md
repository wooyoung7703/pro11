# Bottom-Detection Model — System Design

Goal: Convert the system’s training, inference, calibration, and trading from an “Up/Down next return” classifier to a “Bottom detection” event model, with minimal disruption and reversible rollout.

## 1) Target and Label Definition

Default target: bottom_event at time t means “within lookahead H bars after t, price draws down by ≥D and then rebounds by ≥R” — i.e., a tradeable local bottom.

Parameters (configurable):
- lookahead: int bars, e.g., 30
- drawdown: float (fraction), e.g., 0.005 (−0.5%)
- rebound: float (fraction), e.g., 0.003 (+0.3%) after the bottom within the lookahead window

Label pseudo-code:
```
for each t:
  p0 = close[t]
  w = close[t+1 : t+lookahead]
  j = argmin(w)
  if (w[j] - p0)/p0 <= -drawdown:
      if (max(w[j:]) - w[j])/w[j] >= rebound:
          y[t] = 1 else 0
```

Alternatives supported later: swing-low (fractal), triple-barrier variant, changepoint-based triggers.

## 2) Config and Registry

- AppConfig additions:
  - training_target_type: 'direction' | 'bottom' (default: 'direction')
  - bottom_lookahead: int
  - bottom_drawdown: float
  - bottom_rebound: float
- Model family naming:
  - direction: baseline_predictor
  - bottom: bottom_predictor (new)
- Model Registry metadata:
  - metrics.target = 'bottom'
  - metrics.label_definition (string)
  - metrics.label_params = {lookahead, drawdown, rebound}
  - features (as before)

## 3) Backend Changes

### 3.1 Training
- File: `backend/apps/training/training_service.py`
  - Add dataset builder for bottom target using OHLCV closes (fetch_recent).
  - New method: `run_training_bottom(limit, store, class_weight, cv_splits, time_cv)` returns metrics and stores model as `bottom_predictor`.
  - Metrics: add PR-AUC, recall@precision≥p0, positives ratio, plus existing AUC/Brier/ECE for comparability.
  - Registry write: include target/label_definition/params.
  - Predictors: `predict_latest_bottom()` reuses latest features (and any extra features if available). Output prob=P(bottom), decision=prob≥threshold.

- File: `backend/apps/api/main.py`
  - Extend `TrainingRunRequest` to include `target: 'direction'|'bottom'` and `bottom_{lookahead,drawdown,rebound}` (optional overrides).
  - `/api/training/run`: route to bottom or direction training accordingly.

### 3.2 Inference
- `GET /api/inference/predict` additions:
  - If target is bottom (config or query `target=bottom`), load production/latest model from `bottom_predictor` family.
  - Response adds `target: 'bottom'` and `label_params` echo.
  - `probability` semantics: P(bottom_event). `decision = 1 if prob ≥ threshold else -1` remains, but copy texts updated.
  - Direction card is hidden for bottom target.
- Inference logs (`InferenceLogRepository.bulk_insert` caller): add `extra: { target: 'bottom' }`.

### 3.3 Calibration & Labeler
- Live calibration `/api/inference/calibration/live`:
  - Filter window by `extra.target == 'bottom'` when bottom mode is active. (If schema change is heavy, use current single-target mode and guarantee only one target is emitting logs.)
- Realized labeler `/api/inference/labeler/run`:
  - Add bottom mode: for each unlabeled inference, compute bottom_event with the fixed label_params relative to inference.created_at using OHLCV window; set realized=1/0.
  - Add query params: `target=bottom` and optional lookahead/drawdown/rebound to match training.

### 3.4 Trading
- `/api/trading/no_signal_breakdown`:
  - Switch semantics for bottom: show prob, threshold, decision, and bottom gates state.
- Trading engine gating (runtime params via `/api/trading/live/params` kept):
  - Entry prerequisite: decision==1 (bottom signal).
  - Optional confirmation gate: price rebound ≥ confirm% from local min since signal; OR RSI cross-up; OR candle close above short MA.
  - Cooldown after entry; ATR-based stop (k×ATR below local low) and target (risk multiple or trailing).
  - Scale-in: disabled by default for bottom; if enabled, gate on additional confirmation only.

## 4) Frontend Changes

### 4.1 Inference Playground
- Rename labels to “Bottom probability”, decision interpretation.
- Hide direction forecast card when target=bottom.
- Optional: selector to switch target (direction/bottom) for dev; in prod this mirrors backend config.

### 4.2 Calibration View
- Wording updates only; chart/metrics remain (ECE/Brier). Note rarity of positives and provide bin count guidance.

### 4.3 Model Metrics / Training Jobs
- Show target type in rows. Add training controls to pick target and bottom params.

### 4.4 Admin Console
- Expose bottom params for training run (lookahead/drawdown/rebound). Provide help tooltips explaining semantics.

## 5) Data and API Contracts

- Training run (new fields):
  - Request: `{ target: 'bottom', bottom_lookahead?: int, bottom_drawdown?: float, bottom_rebound?: float, ... }`
  - Response: unchanged, plus `used_target`.
- Predict:
  - Query: `?target=bottom` (optional; else use config)
  - Response: `{ status, target: 'bottom', probability, decision, threshold, model_version, used_production, hint? }`
- Labeler run:
  - Query: `?target=bottom&lookahead=30&drawdown=0.005&rebound=0.003&min_age_seconds=60`

## 6) Testing Plan

- Unit: bottom labeler on synthetic OHLCV; edge cases (no rebound, exact thresholds, boundaries).
- Training smoke: small dataset → non-trivial PR-AUC; registry row contains target and params.
- Inference + realized backfill: insert logs with timestamps → labeler assigns realized → live calibration `status=ok` and bins length.`
- Trading no-signal breakdown: bottom decision path shows expected gates.

## 7) Migration & Rollout

- Phase 1 (dual support):
  - Implement bottom pipeline under `bottom_predictor` without touching direction; add config flag to select active target.
  - Inference logs write `extra.target` (or single-target emission) to isolate calibration.
- Phase 2 (UI toggle & tuning):
  - Surface target selection in Admin only; freeze direction view for reference.
  - Tune threshold with PR curve; monitor ECE drift.
- Phase 3 (go-live):
  - Switch trading to bottom target; enable confirmation gates; set risk params; keep fallback to direction if needed.

## 8) Edge Cases & Guardrails

- Sparse positives → use class_weight/threshold tuning; monitor alerting for over-triggering.
- Label leakage: ensure lookahead strictly future; labeler `min_age_seconds` ≥ bar interval.
- Config mismatches: log target and label_params into `metrics` and inference `extra`.
- Calibration comparability: ECE still valid; add PR-AUC monitoring in /metrics.

## 9) Work Breakdown

- Backend
  - [ ] AppConfig additions + plumbing
  - [ ] TrainingService: bottom dataset + fit + metrics + predict
  - [ ] API: training.run params, inference.predict target, labeler bottom mode
  - [ ] Inference log extra.target + calibration filter
  - [ ] Trading gates + breakdown updates
- Frontend
  - [ ] InferencePlayground copy & hide direction card in bottom mode
  - [ ] Admin/Training UI controls for target & params
  - [ ] ModelMetrics show target
  - [ ] Calibration copy update
- Tests
  - [ ] bottom labeler unit
  - [ ] end-to-end smoke (predict → labeler → live calibration)

Acceptance criteria:
- Predict returns `target='bottom'` and prob in [0,1]; decision consistent with threshold.
- Labeler assigns realized for bottom with configured params; live calibration returns bins.
- Trading shows bottom decision in breakdown and respects gates; entries occur only when both decision and confirmation pass.
- UI reflects bottom wording and hides irrelevant cards.

## Quick usage

- Train bottom model:
  - POST /api/training/run with body {"target":"bottom","bottom_lookahead":30,"bottom_drawdown":0.005,"bottom_rebound":0.003}
  - or GET /api/training/run?target=bottom&bottom_lookahead=30&bottom_drawdown=0.005&bottom_rebound=0.003
- Predict bottom probability:
  - GET /api/inference/predict?target=bottom
  - Response includes {status, probability, decision, threshold, model_version, used_production, target:"bottom"}

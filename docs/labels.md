# Horizon Label Definition (OHLCV-based)

We updated horizon-specific training labels to use OHLCV close prices instead of a proxy.

Definition:
- For a horizon of `k` bars (e.g., 1m/5m/15m), with aligned closed candles:
  - label[t] = 1 if close[t+k] > close[t]
  - label[t] = 0 otherwise

Implementation notes:
- TrainingService.run_training_for_horizon now fetches recent OHLCV candles, aligns by close_time, and computes labels using future closes when available.
- If OHLCV fetch or alignment is unavailable, it falls back to a conservative proxy to avoid crashing, but this path should be rare.

Operational impact:
- Expect improved label accuracy for horizon models (baseline_predictor_<h>). Retrain models with the updated code for best results.
- Metrics now include `label_source`, `label_horizon_steps`, and `label_definition` metadata.

# Project TODOs

This file tracks actionable improvements discussed for calibration/drift CI robustness and adjacent operational tweaks.

## Quick improvements (low-risk, high-impact)

- Auto labeler cadence
  - Increase min_age to 60–120s; batch limit 2000–5000 for faster backfill.
  - Keep AUTO_LABELER_ENABLED=true in dev/prod; wire a simple health metric for last run and labeled count.
- Auto inference sampling
  - Ensure live inference loop runs every 10–15s to generate enough probability samples.
  - If needed, add a small shadow-logging path to persist probabilities even when not crossing decision thresholds.
- Calibration window + bins
  - Use 1–6h windows for live calibration with 6–8 bins (adaptive downward if samples < bins).
  - Enable eager_label on live calibration endpoint to opportunistically reduce no_data responses.
- Data alignment hygiene
  - Keep recent-tail OHLCV backfill and feature backfill targets at 600–1200 to avoid label scarcity.

## Structural improvements (medium risk/effort)

- Confidence intervals for calibration
  - Implement Wilson or Jeffreys intervals per bin; surface CI in reliability table and downstream dashboards.
  - Bootstrap CI for ECE with min per-bin sample thresholds; gray-state the metric when under-sampled.
- Adaptive binning / bin merge
  - Merge adjacent sparse bins to meet a minimum sample threshold; report effective bins in API response.
- Reference distribution strategy
  - Snapshot production calibration at promotion; track EWMA or rolling window baselines; compare live against this reference.
  - Introduce a gray-state when either side is under-sampled to avoid false drift alarms.
- Shadow logging hooks
  - Persist probability + timestamp for live inferences; join realized labels post-lookahead for richer calibration datasets.
  - Add a retention policy and a lightweight verification endpoint.

## Notes

- Artifacts/models are ignored by Git via `.gitignore`. Previously committed artifacts were verified as not tracked locally.
- Bottom-only training is aligned across preview and training gates; promotion thresholds are separated from training thresholds.

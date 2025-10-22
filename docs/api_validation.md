# API Validation — Health and Response Shape

This guide shows how to quickly verify that the backend APIs are up and returning valid response shapes.

- Base URL: http://localhost:8000 (changeable via `API_BASE`)
- Auth header: `X-API-Key: <your_key>` (default dev key is `dev-key`; can be disabled by `DISABLE_API_KEY=1`)

## Quick smoke via script

Use the Python script in `scripts/verify_api_smoke.py` to run a small battery of checks.

Environment variables:
- `API_BASE` (default: `http://localhost:8000`)
- `API_KEY` (default: `dev-key`)

Run:
```bash
# From repo root
python scripts/verify_api_smoke.py
```

The script prints PASS/FAIL per endpoint and exits with non-zero if any critical checks fail.

## Endpoints covered

- Models/Production
  - GET `/api/models/production/metrics` → `{ status: 'ok'|'no_models', model?: { id, version, status, metrics: {} } }`
  - GET `/api/models/production/calibration` → `{ status: 'ok'|'no_models', calibration?: { brier, ece, mce, reliability_bins } }`
- Inference
  - GET `/api/inference/predict?debug=1` → `{ status: 'ok'|'no_data'|..., probability?, model_version?, used_production? }`
- Features
  - GET `/admin/features/status` → `{ status: 'ok', symbol, interval, lag_seconds?, latest_snapshot? }`
  - POST `/admin/features/compute-now` → `{ status: 'ok', result: { ... } }`
- Live Calibration
  - GET `/api/inference/calibration/live?window_seconds=3600&bins=10` → `{ status: 'ok'|'no_data', ece?, brier?, reliability_bins?, attempted_eager_label? }`
    - Behavior: If the window has no realized labels, the server will trigger the labeler in the background.
    - New: It also attempts a bounded, synchronous eager labeling pass by default to reduce `no_data`. When attempted, the response includes `attempted_eager_label: true`.
    - Optional params:
      - `eager_label` (default: true) — set to false to disable the eager attempt.
      - `eager_limit` — override per-call max labels processed during the eager pass (safe-capped).
      - `eager_min_age_seconds` — override the min-age gate for eligible candidates during the eager pass.
- Health
  - GET `/ready` → `{ status: 'ready' }` (200) once the app has started polling and DB is available
  - GET `/metrics` → Prometheus text (200)

Notes:
- If `no_data` appears, it’s often due to missing/latest features or unlabeled inferences. Try `POST /admin/features/compute-now` and then re-run predict; for live calibration, populate labels via `POST /api/inference/labeler/run?force=true&min_age_seconds=60` (if enabled).
- Many endpoints require `X-API-Key`. In dev, default is `dev-key`, unless overridden.

## Minimal manual checks (optional)

- Production metrics:
  - 200 OK; JSON with `status`. If `ok`, ensure `model.version` is non-empty and `metrics` is an object.
- Predict (debug):
  - 200 OK; JSON with `status`.
  - If `ok`, ensure `probability` is in [0,1], `model_version` present, `used_production` is boolean.
- Features status:
  - 200 OK; JSON with `symbol`, `interval`, `lag_seconds` (number), optional `latest_snapshot` times.

Troubleshooting tips are embedded in the backend responses (`hint`, `next_steps`) and surfaced in the Inference Playground UI.

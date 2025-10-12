# Ops Runbook

## Kill switch / Safe pause
- Purpose: Immediately pause live trading actions while keeping read-only endpoints up.
- Lever:
  - ENV toggle: `EXCHANGE_TRADING_ENABLED=false` (requires process reload) OR
  - Admin endpoint (recommended): `POST /admin/pause` → flips runtime flag without restart.
- Resume:
  - Set `EXCHANGE_TRADING_ENABLED=true` or `POST /admin/resume`.
- Warm-up:
  - After resume, observe N minutes without placing orders, only collect signals and verify exchange connectivity and drift.

## Recovery and reconciliation
- On restart or outage:
  1. Fetch recent `myTrades` and open orders from exchange.
  2. Reconcile local DB positions and order states with exchange truth.
  3. Generate audit diff; if drift detected (qty/avg price mismatch), correct local state and log reason.

## Incident triage checklist
- High slippage or error rate:
  - Trigger kill switch, switch to maker-only or disable market orders.
  - Increase MIN_ADD_DISTANCE_BPS and enable EXIT_REQUIRE_NET_PROFIT if off.
- Label coverage drop/`no_data` spike:
  - Check AUTO_LABELER_* settings and DB lag; increase MIN_AGE or batch; backfill.
- Rate limit bursts:
  - Backoff + jitter, enable caching, reduce polling.

## Metrics to watch
- Event lag, dropped deltas, reconnects
- Risk blocks, current DD, daily loss usage
- Slippage avg/p95, order failure rate
- Label coverage / min_age compliance

## Playbooks to automate (future)
- Auto-pause on DD breach or slippage spike
- Auto-retry with idempotency on transient order errors
- Auto-backfill myTrades drift

## Sentiment pipeline ops

### Quick run
- Manual training (with sentiment):
  - POST /api/training/run {"sentiment": true}
  - GET  /api/training/run?sentiment=true
- Ablation (with vs without sentiment):
  - POST /api/training/run {"sentiment": true, "ablate_sentiment": true}
  - GET  /api/training/run?sentiment=true&ablate_sentiment=true
- Status:
  - GET /api/training/status?id=<job_id>
  - Look for `sentiment_report.ablation.delta` in response

### ENV knobs
- TRAINING_INCLUDE_SENTIMENT=true|false
- TRAINING_IMPUTE_SENTIMENT=ffill|zero|none
- TRAINING_SENTIMENT_FEATURES=sent_score,sent_cnt,sent_pos_ratio,sent_d1,sent_d5,sent_vol_30,sent_ema_5m,sent_ema_15m,sent_ema_60m
- TRAINING_ABLATION_REPORT=true|false

### Metrics to watch (sentiment)
- ingest: sentiment_ingest_total, sentiment_ingest_errors_total, sentiment_ingest_latency_ms_bucket
- history: sentiment_history_queries_total, sentiment_history_query_latency_ms_bucket
- sse: sentiment_sse_clients (should be > 0 when dashboards open)
- join quality: feature_sentiment_join_attempts_total vs feature_sentiment_join_missing_total
- dedup: sentiment_dedup_upsert_total

### Alert triage
- Ingest latency p95 high → check upstream provider stalls / DB saturation; backoff and queue length
- History latency p95 high → reduce step/range; confirm DB indexes
- SSE clients zero → dashboard/network issue; verify API key/CORS
- Join missing rate high → widen lookback or verify ingestion freshness; tune SENTIMENT_POS_THRESHOLD/STEP

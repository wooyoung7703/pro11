# Metrics and Alerting (Blueprint)

## What to expose
- Realtime: event_lag_seconds, dropped_deltas_total, reconnects_total
- Trading: risk_blocks_total{reason}, current_drawdown, daily_loss_usage
- Quality: slippage_bps_avg, order_failures_total, label_coverage_ratio, no_data_ratio
- App: http_request_duration_seconds, errors_total

## Configuration
- `METRICS_ENABLED` (bool): enable export endpoint
- `METRICS_HOST` / `METRICS_PORT`: exporter bind address
- Alert toggles (soft): `ALERT_ON_*` vars (real alerts live in Alertmanager)

## Next steps
- Add Prometheus exporter endpoint in backend (e.g., /metrics)
- Build Grafana dashboards for the above metrics
- Add Alertmanager rules: DD breach, order failure spike, label coverage drop

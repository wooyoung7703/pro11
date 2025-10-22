# Metrics and Alerting (Blueprint)

## What to expose
- Realtime: event_lag_seconds, dropped_deltas_total, reconnects_total
- Trading: risk_blocks_total{reason}, current_drawdown, daily_loss_usage
- Quality: slippage_bps_avg, order_failures_total, label_coverage_ratio, no_data_ratio
- App: http_request_duration_seconds, errors_total

### Inference Calibration
- inference_calibration_eager_label_runs_total
	- Counter. Increments when the live calibration endpoint performs a bounded synchronous “eager” labeling attempt to avoid `no_data`.
	- Use to monitor how often on-demand labeling is needed in live flows; a rising trend may suggest labeler throughput or min-age is too conservative.

- inference_calibration_eager_label_runs_by_label_total
	- Counter{symbol, interval, result}. Attempts by label.
	- result: attempted | skipped_lock | error.
	- Use this to understand per-symbol behavior and contention (lock) vs error cases.

## Configuration
- Metrics endpoint: the backend exposes Prometheus metrics at `GET /metrics`.
- Set `APP_PORT` (compose) or uvicorn `--port` accordingly and ensure Prometheus can reach it.
- Alert toggles (soft): `ALERT_ON_*` vars (real alerts live in Alertmanager)

### Prometheus scrape config example
Add a job to your Prometheus config to scrape the backend app:

```yaml
scrape_configs:
	- job_name: 'xrp-backend'
		scrape_interval: 15s
		static_configs:
			- targets: ['app:8000']  # inside Docker compose network
				labels:
					service: 'backend'
			# Or from host:
			# - targets: ['localhost:8000']
		metrics_path: /metrics
```

### Grafana panel queries
- Eager label runs (rate):
	- `rate(inference_calibration_eager_label_runs_total[5m])`
- Eager label runs (increase last 1h):
	- `increase(inference_calibration_eager_label_runs_total[1h])`
- Eager label runs by label (rate):
	- `sum by (symbol, interval, result) (rate(inference_calibration_eager_label_runs_by_label_total[5m]))`

See `docs/grafana_calibration_dashboard.json` for a minimal dashboard you can import and adapt (update the Prometheus datasource UID after import).

## Next steps
- Build Grafana dashboards for the above metrics (starter JSON included)
- Add Alertmanager rules: DD breach, order failure spike, label coverage drop

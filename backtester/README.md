# Simple Backtester (Replay-Only)

Replay real fills over OHLCV to produce auditable events, buy bundles, trades, and transparent counters. Simulation knobs are removed per design: this tool only replays actual trades.

## What you get
- Events: entry, scale_in, partial_exit, full_exit
- Buy bundles: contiguous buys grouped per position
- Trades: entry→exit segments with PnL
- Summary and window counters (JSON)
- CSV exports for events, bundles, trades with normalized timestamps (ms and ISO)

## Inputs
- OHLCV from CSV: columns timestamp,open,high,low,close,volume
- OR OHLCV from HTTP URL returning JSON array of { timestamp, open, high, low, close, volume }
- Fills from CSV: columns ts,side,price,qty,fee
- OR fills from HTTP URL returning JSON array of { ts, side, price, qty, fee }

## URL mode: auth and pagination
- Add bearer token: `--auth-bearer YOUR_TOKEN`
- Add headers (repeatable): `--http-header "X-API-Key: abc"`
- Pagination: if URL contains `{page}`, pages iterate 1..N until empty or `--max-pages` reached

## Examples
- CSV inputs:
	python run_backtest.py \
		--csv sample_data/toy.csv \
		--fills sample_data/fills_toy.csv \
		--events events.csv --bundles bundles.csv --trades trades.csv

- HTTP inputs with auth and pagination:
	python run_backtest.py \
		--csv-url "https://api.yourhost/candles?symbol=BTCUSDT&tf=1m&page={page}" \
		--fills-url "https://api.yourhost/fills?symbol=BTCUSDT&page={page}" \
		--auth-bearer YOUR_TOKEN \
		--max-pages 50 \
		--events events.csv --bundles bundles.csv --trades trades.csv

## Output
- Prints JSON to stdout with overall summary + window_counts
- Writes CSVs if paths provided via `--events`, `--bundles`, `--trades`

Timestamps are enriched with `*_ms` and `*_iso` fields to ensure consistent display in the UI.

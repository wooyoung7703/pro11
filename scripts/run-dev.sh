#!/usr/bin/env sh
set -e
HOST="${UVICORN_HOST:-0.0.0.0}"
PORT="${UVICORN_PORT:-8000}"
RELOAD_FLAG="${DEV_RELOAD:-1}"
EXTRA_OPTS="${UVICORN_EXTRA_OPTS:-}"

# Dev safety guard: enforce safe defaults for non-prod runs
if [ "${APP_ENV:-local}" != "prod" ]; then
  # Disable real trading in non-prod by default; allow explicit opt-in via DEV_ALLOW_EXCHANGE_TRADING=1
  if [ "${EXCHANGE_TRADING_ENABLED:-}" = "true" ] && [ "${DEV_ALLOW_EXCHANGE_TRADING:-0}" != "1" ]; then
    echo "[run-dev] Forcing EXCHANGE_TRADING_ENABLED=false for non-prod (set DEV_ALLOW_EXCHANGE_TRADING=1 to enable)"
    export EXCHANGE_TRADING_ENABLED=false
  fi
  # Ensure testnet or mock in non-prod unless explicitly allowed to hit mainnet
  if [ "${EXCHANGE:-binance}" != "mock" ] && [ "${BINANCE_TESTNET:-}" != "true" ]; then
    if [ "${DEV_ALLOW_MAINNET:-0}" = "1" ]; then
      echo "[run-dev] DEV_ALLOW_MAINNET=1 -> honoring BINANCE_TESTNET=false (mainnet)"
    else
      echo "[run-dev] Forcing BINANCE_TESTNET=true for non-prod (set DEV_ALLOW_MAINNET=1 to allow mainnet)"
      export BINANCE_TESTNET=true
    fi
  fi
  # Provide sane AUTO_LABELER defaults if unset/empty
  if [ -z "${AUTO_LABELER_INTERVAL:-}" ]; then export AUTO_LABELER_INTERVAL=15; fi
  if [ -z "${AUTO_LABELER_MIN_AGE_SECONDS:-}" ]; then export AUTO_LABELER_MIN_AGE_SECONDS=120; fi
  if [ -z "${AUTO_LABELER_BATCH_LIMIT:-}" ]; then export AUTO_LABELER_BATCH_LIMIT=1000; fi
fi

# Safety guard: run checks if available
if [ -f "scripts/safety_check.sh" ]; then
  echo "[run-dev] Running safety checks..."
  if ! bash scripts/safety_check.sh; then
    if [ "${DEV_ALLOW_EXCHANGE_TRADING:-0}" = "1" ]; then
      echo "[run-dev] Safety checks failed, but DEV_ALLOW_EXCHANGE_TRADING=1 -> proceeding for dev opt-in."
    else
      echo "[run-dev] Safety checks failed. Aborting."
      exit 1
    fi
  fi
fi

printf "[run-dev] Starting uvicorn host=%s port=%s reload=%s extra='%s'\n" "$HOST" "$PORT" "$RELOAD_FLAG" "$EXTRA_OPTS"

BASE_CMD="poetry run uvicorn backend.apps.api.main:app --host $HOST --port $PORT"
if [ "$RELOAD_FLAG" = "1" ]; then
  BASE_CMD="$BASE_CMD --reload --reload-dir /app/backend --reload-delay 0.5"
fi
# Optional auto-bootstrap for local dev
if [ "${AUTO_BOOTSTRAP_LOCAL:-0}" = "1" ]; then
  echo "[run-dev] AUTO_BOOTSTRAP_LOCAL=1 -> running scripts/auto_bootstrap_local.py in background"
  # Use API_BASE to point to the container port (8000) for in-pod access
  API_BASE="http://localhost:8000" API_KEY="${API_KEY:-dev-key}" BOOTSTRAP_BACKFILL_BARS="${BOOTSTRAP_BACKFILL_BARS:-5000}" \
    nohup python3 scripts/auto_bootstrap_local.py >/tmp/auto_bootstrap.log 2>&1 &
fi
# Allow arbitrary extra flags (e.g. --log-level debug)
# shellcheck disable=SC2086
exec sh -c "$BASE_CMD $EXTRA_OPTS"

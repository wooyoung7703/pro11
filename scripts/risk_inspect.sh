#!/usr/bin/env bash
# Print current pre-trade risk configuration from .env or .env.safety
set -euo pipefail

ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
  if [ -f ".env.safety" ]; then
    ENV_FILE=".env.safety"
  else
    echo "No .env or .env.safety found"; exit 1
  fi
fi

grep -E '^(RISK_|LIVE_TRADE_BASE_SIZE|TRADING_FEE_|SYMBOL=|INTERVAL=)' "$ENV_FILE" | sed 's/^/ - /'

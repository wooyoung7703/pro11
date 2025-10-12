#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "[dev] Stopping dev stack" >&2
exec docker compose -f docker-compose.dev.yml down "$@"

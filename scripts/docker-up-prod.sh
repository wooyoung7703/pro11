#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "[prod] Bringing up prod stack using docker-compose.yml" >&2
exec docker compose -f docker-compose.yml up -d "$@"

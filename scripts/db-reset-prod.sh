#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "[prod][reset] Stopping prod stack & removing volumes" >&2
docker compose -f docker-compose.yml down -v || true

echo "[prod][reset] Starting only database (fresh volume)" >&2
docker compose -f docker-compose.yml up -d db

ATTEMPTS=20
until docker compose -f docker-compose.yml ps | grep db | grep -qi healthy; do
  ATTEMPTS=$((ATTEMPTS-1))
  if [ $ATTEMPTS -le 0 ]; then
    echo "[prod][reset] DB did not become healthy in time" >&2
    exit 1
  fi
  sleep 1
  echo "[prod][reset] waiting for db health..." >&2
done

echo "[prod][reset] Running migrations" >&2
docker compose -f docker-compose.yml run --rm app poetry run alembic upgrade head || {
  echo "[prod][reset] WARNING: migration failed" >&2
}

echo "[prod][reset] Starting full prod stack" >&2
docker compose -f docker-compose.yml up -d app

APP_PORT=${APP_PORT:-8000}
echo "[prod][reset] Done. Health check: curl http://localhost:${APP_PORT}/healthz" >&2

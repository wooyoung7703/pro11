#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

export COMPOSE_PROJECT_NAME=devstack

echo "[dev][reset] Stopping dev stack & removing volumes" >&2
docker compose -f docker-compose.dev.yml down -v || true

echo "[dev][reset] Starting only database (fresh volume)" >&2
docker compose -f docker-compose.dev.yml up -d db

# Wait for DB healthy
ATTEMPTS=20
until docker compose -f docker-compose.dev.yml ps | grep db | grep -qi healthy; do
  ATTEMPTS=$((ATTEMPTS-1))
  if [ $ATTEMPTS -le 0 ]; then
    echo "[dev][reset] DB did not become healthy in time" >&2
    exit 1
  fi
  sleep 1
  echo "[dev][reset] waiting for db health..." >&2
done

echo "[dev][reset] Running migrations" >&2
docker compose -f docker-compose.dev.yml run --rm app poetry run alembic upgrade head || {
  echo "[dev][reset] WARNING: migration failed" >&2
}

echo "[dev][reset] Starting full dev stack (app)" >&2
docker compose -f docker-compose.dev.yml up -d app

APP_PORT=${APP_PORT:-8000}
echo "[dev][reset] Done. Health check: curl http://localhost:${APP_PORT}/healthz" >&2

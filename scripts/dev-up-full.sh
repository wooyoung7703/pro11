#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

COMPOSE_FILE=docker-compose.dev.yml
FAST_UPGRADE=${FAST_UPGRADE:-true}
RESET=0
NO_BUILD=0

usage() {
  cat <<EOF
Usage: $0 [options]
  -r, --reset        down -v before starting
  -n, --no-build     skip build (use existing images)
  -u, --upgrade      call /admin/fast_startup/upgrade at end (default when FAST_UPGRADE=true)
  -U, --no-upgrade   do not call upgrade endpoint
  -h, --help         show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -r|--reset) RESET=1; shift;;
    -n|--no-build) NO_BUILD=1; shift;;
    -u|--upgrade) FAST_UPGRADE=true; shift;;
    -U|--no-upgrade) FAST_UPGRADE=false; shift;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg $1" >&2; usage; exit 1;;
  esac
done

if [ $RESET -eq 1 ]; then
  echo "[dev-up-full] down -v" >&2
  docker compose -f $COMPOSE_FILE down -v || true
fi

if [ $NO_BUILD -eq 0 ]; then
  echo "[dev-up-full] building app image" >&2
  docker compose -f $COMPOSE_FILE build app
fi

echo "[dev-up-full] starting db" >&2
docker compose -f $COMPOSE_FILE up -d db

# Wait for health
ATTEMPTS=30
until docker compose -f $COMPOSE_FILE ps | grep db | grep -qi healthy; do
  ATTEMPTS=$((ATTEMPTS-1))
  if [ $ATTEMPTS -le 0 ]; then
    echo "[dev-up-full] DB not healthy in time" >&2
    exit 1
  fi
  sleep 1
  echo "[dev-up-full] waiting for db..." >&2
done

echo "[dev-up-full] migrations" >&2
./scripts/migrate-docker.sh upgrade head

echo "[dev-up-full] starting app" >&2
docker compose -f $COMPOSE_FILE up -d app

# Basic readiness (fast_startup may skip components)
./scripts/dev-health.sh localhost:8000 20 1 || true

if [ "$FAST_UPGRADE" = "true" ]; then
  echo "[dev-up-full] upgrading from FAST_STARTUP (if active)" >&2
  curl -s -X POST http://localhost:8000/admin/fast_startup/upgrade | jq . 2>/dev/null || true
fi

echo "[dev-up-full] done" >&2

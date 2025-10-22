#!/usr/bin/env bash
# Rebuild local-dev images (no cache), recreate containers and volumes fresh
set -euo pipefail
cd "$(dirname "$0")/.."

# Ensure Docker is available
if ! docker info >/dev/null 2>&1; then
  echo "[rebuild-local] Docker daemon is not running. Please start Docker Desktop and re-run." >&2
  exit 1
fi

# Detect compose
if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "[rebuild-local] docker compose/docker-compose not found" >&2
  exit 1
fi

export COMPOSE_PROJECT_NAME=pro11local

# 1) Stop and remove containers + named volumes
"${COMPOSE_CMD[@]}" -f docker-compose.local.yml down -v --remove-orphans || true

# 2) Rebuild images without cache and pull latest bases
"${COMPOSE_CMD[@]}" -f docker-compose.local.yml build --pull --no-cache

# 3) Bring up stack
"${COMPOSE_CMD[@]}" -f docker-compose.local.yml up -d

echo "[rebuild-local] Done. Fresh images/containers/volumes created for local dev (ports: 8010/8081/55442)."
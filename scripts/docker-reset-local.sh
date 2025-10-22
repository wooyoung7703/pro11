#!/usr/bin/env bash
# Reset only the local-dev stack (stops containers, removes volumes/networks of local compose)
set -euo pipefail
cd "$(dirname "$0")/.."

# Detect Docker availability
if ! docker info >/dev/null 2>&1; then
  echo "[reset-local] Docker daemon is not running. Please start Docker Desktop and re-run." >&2
  exit 1
fi

# Detect compose command
if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "[reset-local] docker compose/docker-compose not found" >&2
  exit 1
fi

# Use a dedicated project name used by our up script
export COMPOSE_PROJECT_NAME=dev

# Bring down local stack and remove volumes/networks
"${COMPOSE_CMD[@]}" -f docker-compose.local.yml down -v --remove-orphans || true

# Remove containers/networks left with project label just in case
if docker ps -aq -f label=com.docker.compose.project=dev >/dev/null 2>&1; then
  ids=$(docker ps -aq -f label=com.docker.compose.project=dev || true)
  if [[ -n "${ids}" ]]; then docker rm -f ${ids} || true; fi
fi

# Optional: prune dangling networks for the local project
networks=$(docker network ls --filter label=com.docker.compose.project=dev -q || true)
if [[ -n "${networks}" ]]; then docker network rm ${networks} || true; fi

echo "[reset-local] Done. Local dev stack cleaned (project=dev)."
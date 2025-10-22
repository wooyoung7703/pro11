#!/usr/bin/env bash
# Project-wide Docker reset. Stops all known stacks and removes their volumes (local/dev/prod/default).
set -euo pipefail
cd "$(dirname "$0")/.."

if ! docker info >/dev/null 2>&1; then
  echo "[reset] Docker daemon is not running. Please start Docker Desktop and re-run." >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "[reset] docker compose/docker-compose not found" >&2
  exit 1
fi

# Known compose files and project names
# default
"${COMPOSE_CMD[@]}" -f docker-compose.yml down -v --remove-orphans || true
# dev variants
"${COMPOSE_CMD[@]}" -f docker-compose.dev.yml down -v --remove-orphans || true
"${COMPOSE_CMD[@]}" -f docker-compose.dev.override.yml down -v --remove-orphans || true
"${COMPOSE_CMD[@]}" -f docker-compose.dev.deploy.yml down -v --remove-orphans || true
"${COMPOSE_CMD[@]}" -f docker-compose.dev.watch.yml down -v --remove-orphans || true
# prod
"${COMPOSE_CMD[@]}" -f docker-compose.prod.yml down -v --remove-orphans || true
# local (named project)
COMPOSE_PROJECT_NAME=pro11local "${COMPOSE_CMD[@]}" -f docker-compose.local.yml down -v --remove-orphans || true

# Remove containers and networks associated with common project names
for proj in pro11 pro11local; do
  ids=$(docker ps -aq -f label=com.docker.compose.project=${proj} || true)
  if [[ -n "${ids}" ]]; then docker rm -f ${ids} || true; fi
  nets=$(docker network ls -q --filter label=com.docker.compose.project=${proj} || true)
  if [[ -n "${nets}" ]]; then docker network rm ${nets} || true; fi
  vols=$(docker volume ls -q --filter label=com.docker.compose.project=${proj} || true)
  if [[ -n "${vols}" ]]; then docker volume rm ${vols} || true; fi
done

echo "[reset] Done. All compose stacks brought down and project volumes cleaned where applicable."
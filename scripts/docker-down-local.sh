#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Use a consistent compose project name, but don't rely solely on it
export COMPOSE_PROJECT_NAME=dev

echo "[down-local] docker compose down (local)"
docker compose -f docker-compose.local.yml down -v --remove-orphans || true

# Fallback: explicitly stop/remove containers if they are still up (project mismatch or prior runs)
echo "[down-local] fallback: removing lingering *-local containers if any"
to_remove=(pro11-frontend-local pro11-app-local pro11-postgres-local)
for name in "${to_remove[@]}"; do
	if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
		echo "[down-local] removing ${name}"
		docker rm -f "${name}" || true
	fi
done

echo "[down-local] done."
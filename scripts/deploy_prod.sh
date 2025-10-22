#!/usr/bin/env bash
set -euo pipefail

# Usage: GH_OWNER=<owner> DEPLOY_PATH=~/pro11 ./scripts/deploy_prod.sh
GH_OWNER="${GH_OWNER:-${COMPOSE_PROJECT_NAME:-}}"
if [[ -z "${GH_OWNER}" ]]; then
  echo "GH_OWNER is required (or set COMPOSE_PROJECT_NAME)" >&2
  exit 1
fi
DEPLOY_PATH="${DEPLOY_PATH:-$HOME/pro11}"

cd "${DEPLOY_PATH}"
export COMPOSE_PROFILES=prod
export GH_OWNER

echo "[deploy] pulling images for owner='${GH_OWNER}' in ${DEPLOY_PATH}"
docker compose -f docker-compose.prod.yml pull

echo "[deploy] starting containers"
docker compose -f docker-compose.prod.yml up -d --remove-orphans

echo "[deploy] pruning old images"
docker image prune -f || true

echo "[deploy] done"
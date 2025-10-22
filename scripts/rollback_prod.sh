#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   BACKEND_TAG=sha-abc1234 FRONTEND_TAG=sha-abc1234 GH_OWNER=<owner> DEPLOY_PATH=~/pro11 ./scripts/rollback_prod.sh

GH_OWNER="${GH_OWNER:-${COMPOSE_PROJECT_NAME:-}}"
if [[ -z "${GH_OWNER}" ]]; then
  echo "GH_OWNER is required (or set COMPOSE_PROJECT_NAME)" >&2
  exit 1
fi

BACKEND_TAG="${BACKEND_TAG:-}"
FRONTEND_TAG="${FRONTEND_TAG:-}"
if [[ -z "${BACKEND_TAG}" && -z "${FRONTEND_TAG}" ]]; then
  echo "Provide at least one of BACKEND_TAG or FRONTEND_TAG" >&2
  exit 1
fi

DEPLOY_PATH="${DEPLOY_PATH:-$HOME/pro11}"
cd "${DEPLOY_PATH}"

export GH_OWNER
export COMPOSE_PROFILES=prod
export BACKEND_TAG
export FRONTEND_TAG

echo "[rollback] using BACKEND_TAG='${BACKEND_TAG:-<keep>}' FRONTEND_TAG='${FRONTEND_TAG:-<keep>}'"
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --no-deps app frontend
docker image prune -f || true

echo "[rollback] complete"

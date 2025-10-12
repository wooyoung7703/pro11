#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

export COMPOSE_PROJECT_NAME=devstack

echo "[dev] Using project name: $COMPOSE_PROJECT_NAME" >&2
echo "[dev] Bringing up dev stack (docker-compose.dev.yml)" >&2
echo "[dev] If you changed dependencies, run: docker compose -f docker-compose.dev.yml build app" >&2
exec docker compose -f docker-compose.dev.yml up -d "$@"

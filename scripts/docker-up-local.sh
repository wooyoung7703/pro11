#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME=dev
exec docker compose -f docker-compose.local.yml up -d --build
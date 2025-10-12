#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.dev.yml}
SERVICE=${SERVICE:-app}
ALEMBIC_INI=alembic.ini

# If root alembic.ini not in image, fallback to backend/migrations/alembic.ini
FALLBACK_INI=backend/migrations/alembic.ini
CMD=${1:-}
if [ -z "$CMD" ]; then
  echo "Usage: $0 <alembic-subcommand> [args...]" >&2
  echo "Examples:" >&2
  echo "  $0 upgrade head" >&2
  echo "  $0 revision -m 'add table'" >&2
  exit 1
fi
shift || true

echo "[migrate-docker] Ensuring $SERVICE container image present..." >&2
docker compose -f "$COMPOSE_FILE" build "$SERVICE" >/dev/null 2>&1 || true

# Decide which ini path exists inside container
INI_PATH=$ALEMBIC_INI
if ! docker compose -f "$COMPOSE_FILE" run --rm "$SERVICE" test -f "$ALEMBIC_INI" 2>/dev/null; then
  INI_PATH=$FALLBACK_INI
fi
echo "[migrate-docker] Using alembic ini: $INI_PATH" >&2

docker compose -f "$COMPOSE_FILE" run --rm "$SERVICE" poetry run alembic -c "$INI_PATH" "$CMD" "$@"
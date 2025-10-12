#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Usage:
#  ./scripts/migrate.sh upgrade head
#  ./scripts.migrate.sh downgrade -1
#  ./scripts/migrate.sh revision -m "message"

CMD=${1:-}
if [ -z "$CMD" ]; then
  echo "Usage: $0 <alembic-subcommand> [args...]" >&2
  echo "Examples:" >&2
  echo "  $0 upgrade head" >&2
  echo "  $0 revision -m 'add table'" >&2
  exit 1
fi

shift || true

if ! command -v poetry >/dev/null 2>&1; then
  echo "[migrate] poetry not found locally. Use container helper instead:" >&2
  echo "  ./scripts/migrate-docker.sh $CMD $@" >&2
  exit 127
fi

poetry run alembic -c alembic.ini "$CMD" "$@"
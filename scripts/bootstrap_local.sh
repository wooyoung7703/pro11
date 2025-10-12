#!/usr/bin/env bash
set -euo pipefail

# Simple local bootstrap: create venv, install poetry locally (if missing), install deps, run migrations, run tests.
# Usage: bash scripts/bootstrap_local.sh [--no-tests] [--no-migrate] [--prod]
#   --no-tests   Skip running pytest
#   --no-migrate Skip alembic upgrade
#   --prod       Install only main (no dev) dependencies

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN=${PYTHON_BIN:-python}
VENV_DIR=".venv"
RUN_TESTS=1
RUN_MIGRATE=1
INSTALL_GROUP="--with dev"

for arg in "$@"; do
  case "$arg" in
    --no-tests) RUN_TESTS=0 ;; 
    --no-migrate) RUN_MIGRATE=0 ;;
    --prod) INSTALL_GROUP="--only main" ;;
  esac
  shift || true
done

if [ ! -d "$VENV_DIR" ]; then
  echo "[bootstrap] Creating virtualenv ($VENV_DIR)..."
  $PYTHON_BIN -m venv "$VENV_DIR"
fi

# Activate venv (works in Git Bash / WSL)
# shellcheck disable=SC1091
source "$VENV_DIR/Scripts/activate" 2>/dev/null || source "$VENV_DIR/bin/activate"

PY_VER=$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "${PY_VER}" != "3.11" ]; then
  if [ "${BOOTSTRAP_ALLOW_PYTHON_MISMATCH:-0}" != "1" ]; then
    echo "[bootstrap][ERROR] Detected Python ${PY_VER}. Required: 3.11.x (set BOOTSTRAP_ALLOW_PYTHON_MISMATCH=1 to override at your own risk)." >&2
    exit 2
  else
    echo "[bootstrap][WARN] Proceeding with unsupported Python ${PY_VER} (override flag set)." >&2
  fi
fi

# Ensure build tooling updated early to get wheels
python -m pip install --upgrade pip setuptools wheel >/dev/null

# Install poetry inside venv if missing
if ! command -v poetry >/dev/null 2>&1; then
  echo "[bootstrap] Installing poetry inside venv..."
  python -m pip install poetry >/dev/null
fi

echo "[bootstrap] Installing dependencies ($INSTALL_GROUP)..."
poetry install --no-root $INSTALL_GROUP || {
  echo "[bootstrap][INFO] Poetry install failed—attempting pip fallback export." >&2
  poetry export -f requirements.txt --without-hashes -o /tmp/requirements.txt 2>/dev/null || {
    echo "[bootstrap][ERROR] Could not export requirements via poetry." >&2
    exit 1
  }
  python -m pip install -r /tmp/requirements.txt || {
    echo "[bootstrap][ERROR] pip fallback install also failed. See logs above." >&2
    exit 1
  }
}

echo "[bootstrap] Ensuring alembic migrations config exists..."
if [ $RUN_MIGRATE -eq 1 ]; then
  poetry run alembic -c backend/migrations/alembic.ini upgrade head
fi

if [ $RUN_TESTS -eq 1 ]; then
  echo "[bootstrap] Running tests..."
  poetry run pytest -q || { echo "[bootstrap] Tests failed"; exit 1; }
fi

echo "[bootstrap] Done. Activate environment with:"
echo "  source $VENV_DIR/Scripts/activate  # (Windows Git Bash)" 
echo "  source $VENV_DIR/bin/activate      # (Linux/WSL)" 

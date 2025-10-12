# Local Development (No Global Poetry Install)

This guide shows a frictionless path to run tests locally on Windows (Git Bash) or WSL without installing Poetry globally.

## TL;DR

1. Copy env template:
   cp .env.example .env
2. Start Postgres (ensure credentials in `.env` match). 
3. Run bootstrap script:
   bash scripts/bootstrap_local.sh
4. Activate venv for subsequent manual commands:
   source .venv/Scripts/activate   # Windows Git Bash

## What The Script Does
- Creates a local `.venv` if missing.
- Installs Poetry INSIDE that venv (isolated, no --user needed).
- Installs dependencies (main + dev by default). Use `--prod` to skip dev deps.
- Applies Alembic migrations (unless `--no-migrate`).
- Runs tests (unless `--no-tests`).

## Script Options
bash scripts/bootstrap_local.sh [--no-tests] [--no-migrate] [--prod]

Examples:
- Fast dependency setup only (no tests):
  bash scripts/bootstrap_local.sh --no-tests
- Production deps only (e.g., reproducible prod footprint):
  bash scripts/bootstrap_local.sh --prod --no-tests

## Postgres Setup Quick Reference
If you already have Postgres running locally, ensure the database exists:

psql -h 127.0.0.1 -U postgres -c "CREATE DATABASE ml_platform;" || true

Migrations are applied automatically by the script (unless you skip). You can re-run:

poetry run alembic -c backend/migrations/alembic.ini upgrade head

## Running the API Locally
After activation:

poetry run uvicorn backend.apps.api.main:app --reload --port 8000

Visit: http://127.0.0.1:8000/docs

## Optional: SKIP_DB Mode
If you want to just explore non-DB endpoints / import graph quickly, set in `.env`:

SKIP_DB=true

DB-dependent endpoints and background tasks will be inert or fail fast, but the app will start.

## Troubleshooting
| Symptom | Cause | Fix |
|---------|-------|-----|
| `psycopg` / `asyncpg` build errors | Missing build tools | Install build tools (Windows: Visual Studio Build Tools; WSL: `sudo apt-get install build-essential`) |
| `poetry: command not found` after bootstrap | Virtualenv not activated | `source .venv/Scripts/activate` (Windows Git Bash) |
| Tests hang | DB not reachable | Check Postgres is running & env vars correct |
| Migration errors | Stale migration state | Drop DB or run `alembic downgrade base && upgrade head` |

## Manual Without Script (Fallback)
If you prefer not to use the script:

python -m venv .venv
source .venv/Scripts/activate  # or .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install poetry
poetry install --no-root --with dev
poetry run alembic -c backend/migrations/alembic.ini upgrade head
poetry run pytest -q

## Keeping Dependencies Reproducible
The `poetry.lock` governs exact versions. CI / Docker builds will remain consistent with your local environment.

## Next Improvements (Optional)
- Add `make local` target wrapping bootstrap
- Export `requirements.txt` for pure-pip deployments (`poetry export -f requirements.txt --without-hashes -o requirements.txt`)
- Multi-stage minimal production image

---
Happy hacking!

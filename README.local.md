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

## Docker-based Dev (staging-like)

We provide a dev deploy compose that mirrors staging, but now without auto-sync by default. Local file edits will NOT auto-reflect into containers unless you opt in.

- Default (no auto sync):

   docker compose -f docker-compose.dev.deploy.yml up -d

- Opt-in watch/sync (explicit):

   docker compose -f docker-compose.dev.deploy.yml -f docker-compose.dev.watch.yml up -d --watch

Notes
- The watch file (`docker-compose.dev.watch.yml`) only adds `develop.watch` sections for app/frontend; it doesn’t change images or ports.
- If you’ve been relying on auto-sync previously, switch to the second command for live iteration.

### Dev override (bind mounts) usage

We also moved the previous auto-loaded override to an opt-in file `docker-compose.dev.override.yml`.

- Use explicitly:

   docker compose -f docker-compose.yml -f docker-compose.dev.override.yml up -d

- If `frontend-dev` was already running from before, stop and remove it:

   docker compose stop frontend-dev && docker compose rm -f frontend-dev || true

## Observability (Prometheus + Grafana)

We include a simple local observability stack in `docker-compose.dev.yml`:

- Prometheus (http://localhost:9090) scrapes the backend at `/metrics`.
- Grafana (http://localhost:3000, admin/admin) can visualize counters.

Start services:

```bash
docker compose -f docker-compose.dev.yml up -d prometheus grafana
```

Prometheus config lives at `infra/prometheus/prometheus.yml`.

Quick queries:

- Global eager-label counter (rate):
   - `rate(inference_calibration_eager_label_runs_total[5m])`
- Labeled eager-label counter (rate):
   - `sum by (symbol, interval, result) (rate(inference_calibration_eager_label_runs_by_label_total[5m]))`

Grafana starter dashboard:

- Import `docs/grafana_calibration_dashboard.json` and change the datasource UID to your Prometheus datasource.

Networking tips:

- Inside Docker network, use `http://app:8000` to reach the backend.
- From the host, use `http://localhost:8000`.

Dev proxy tip:

- If you want to hit backend endpoints directly via the Vite dev server (e.g. `http://localhost:5173/api/...`), set an API key so the proxy can inject it:

   ```bash
   # in frontend/.env.local or your shell
   export VITE_DEV_API_KEY="<your-api-key>"
   npm run dev
   ```

- With `VITE_DEV_API_KEY` set, the dev proxy forwards `X-API-Key` so direct browser requests to `/api`, `/admin` succeed.

Dockerized frontend + host backend:

- If you run the frontend in Docker but the backend on your host (VS Code task), set:

   ```bash
   # .env (repo root) or your shell before compose up
   export VITE_BACKEND_URL="http://host.docker.internal:8000"
   docker compose -f docker-compose.dev.yml up -d frontend
   ```

- This points the Vite proxy inside the container at your host backend. Remove or change it back to `http://app:8000` when you also run the backend in Docker.

## Data Retention (Dev-only helper)

A lightweight retention worker is available in `docker-compose.dev.yml` as service `retention`. It periodically calls the backend admin purge endpoint to delete old rows from `trading_signals` and `autopilot_event_log`.

Start it (alongside the app):

```bash
docker compose -f docker-compose.dev.yml up -d retention
```

Environment knobs:

- `RETENTION_ENABLED` (default: `1`)
- `RETENTION_INTERVAL_SECONDS` (default: `604800` = 7 days)
- `RETENTION_OLDER_THAN_DAYS` (default: `30`)
- `RETENTION_MAX_ROWS` (default: `5000`)
- `RETENTION_TABLES` (default: `trading_signals,autopilot_event_log`)
- `RETENTION_API_KEY` or `API_KEY` to authorize admin endpoint

It uses `BACKEND_URL=http://app:8000` inside the Docker network. Script entry point: `scripts/jobs/purge_worker.py`.

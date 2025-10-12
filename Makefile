POETRY=poetry
PYTHON=${POETRY} run python
APP=backend.apps.api.main:app

.PHONY: install dev run api fmt lint type test migrate revision docker-build docker-build-dev docker-run docker-test

install:
	${POETRY} install --no-root

deV: install

run:
	${POETRY} run uvicorn ${APP} --reload --port 8000

fmt:
	${POETRY} run ruff check --fix .
	${POETRY} run ruff format . || true

lint:
	${POETRY} run ruff check .

type:
	${POETRY} run mypy .

test:
	${POETRY} run pytest -q

docker-build:
	docker build -t xrp-app:latest .

docker-build-dev:
	docker build --build-arg DEV_DEPS=true -t xrp-app-dev:latest .

docker-run:
	docker run --rm -p 8000:8000 --name xrp-app xrp-app:latest

# 컨테이너 내에서 테스트 실행 (dev 이미지 필요)
docker-test: docker-build-dev
	docker run --rm \
	 -e AUTO_PROMOTE_ENABLED=true \
	 -e CALIBRATION_MONITOR_ENABLED=true \
	 xrp-app-dev:latest poetry run pytest -q

revision:
	${POETRY} run alembic -c backend/migrations/alembic.ini revision -m "${m}"

migrate:
	${POETRY} run alembic -c backend/migrations/alembic.ini upgrade head

# Docker Compose helpers
.PHONY: docker-up docker-down docker-logs docker-rebuild

docker-up: ## Start postgres + app stack
	docker compose up -d

docker-down: ## Stop stack
	docker compose down

docker-logs: ## Tail app logs
	docker compose logs -f app

docker-rebuild: ## Rebuild app image only
	docker compose build app

.PHONY: docker-up-dev docker-down-dev docker-logs-dev docker-rebuild-dev docker-up-prod docker-down-prod

docker-up-dev: ## Start dev stack with hot reload
	docker compose -f docker-compose.dev.yml up -d

docker-down-dev: ## Stop dev stack
	docker compose -f docker-compose.dev.yml down

docker-logs-dev: ## Tail dev app logs
	docker compose -f docker-compose.dev.yml logs -f app

docker-rebuild-dev: ## Rebuild dev app image
	docker compose -f docker-compose.dev.yml build app

docker-up-prod: ## Start prod stack
	docker compose -f docker-compose.yml up -d

docker-down-prod: ## Stop prod stack
	docker compose -f docker-compose.yml down

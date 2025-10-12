# Docker / Compose Guide

This document explains how to run the service plus Postgres via docker-compose for local integration testing and to help reproduce the asyncpg pool issue you observed on Windows.

## 1. Prerequisites
- Docker / Docker Desktop installed
- (Optional) Make installed for helper targets

## 2. Files Added
- `docker-compose.yml` – Orchestrates `db` (Postgres 15) and `app` (FastAPI service).
- `.env.docker.example` – Sample environment variables. Copy to `.env.docker` if you later parameterize compose with `${VAR}` syntax.

## 3. Quick Start
```bash
# From repo root
# 1. Build images
docker compose build

# 2. Start stack (detached)
docker compose up -d

# 3. Follow logs
docker compose logs -f app

# 4. Hit readiness endpoint (should show db status)
curl http://localhost:8000/health/ready
```

If everything is healthy you should see JSON including `db` status. If `degraded` appears, the pool still failed — this helps compare with Windows native behavior.

### 3.0 Environment Variable Driven Configuration
Compose files now use parameter substitution; create a `.env.docker` (or reuse your root `.env`) with keys like:
```
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=mydata
POSTGRES_USER=postgres
POSTGRES_PASSWORD=traderpass
DB_EXTERNAL_PORT=5432
DEV_DB_EXTERNAL_PORT=55432
APP_PORT=8000
FAST_STARTUP=true
```
Changing DB credentials? You must also reset the Postgres volume so the cluster re-initializes with the new user/password:
```
docker compose -f docker-compose.yml down -v   # or docker-compose.dev.yml
docker compose -f docker-compose.yml up -d --build
```
If you forget `-v` the old data directory still expects the previous credentials and auth will fail.

Legacy variable names: the code accepts both `POSTGRES_*` and legacy `DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME`. `POSTGRES_*` takes precedence; if absent, the `DB_*` values are used. This lets you keep a local `.env` that already had `DB_HOST=...` without breaking.

### 3.0.1 Default Credentials Update
Default compose fallbacks now use:
```
POSTGRES_USER=postgres
POSTGRES_PASSWORD=traderpass
POSTGRES_DB=wooyoung
```
Override by defining the same variable names in `.env` / `.env.docker`. After changing these for an existing volume run:
```
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d --build
```
so Postgres initializes with the new cluster owner & database.

### 3.0.2 Alembic Migration Helper
Root에 `alembic.ini` 를 추가했고 다음 스크립트로 마이그레이션을 단순화:
로컬 Poetry 환경이 있을 때:
```
./scripts/migrate.sh upgrade head
```
Poetry 가 로컬에 없거나 컨테이너로 실행하고 싶다면:
```
./scripts/migrate-docker.sh upgrade head
```
새 revision:
```
./scripts/migrate-docker.sh revision -m "add new table"
```
직접 run 사용 예:
```
docker compose -f docker-compose.dev.yml run --rm app poetry run alembic -c alembic.ini upgrade head
```
`FAILED: No 'script_location' key found` → 이미지 안에 `alembic.ini` 가 없거나 잘못된 경로. 지금은 Dockerfile 이 루트 `alembic.ini` 를 복사하므로 `-c alembic.ini` 로 동작해야 합니다. 자동 스크립트는 없으면 `backend/migrations/alembic.ini` 로 대체.

### 3.0.3 POSTGRES_HOST 주의
컨테이너 내부에서 DB 서비스는 docker 네트워크 이름 `db` 로 접근해야 합니다. 루트 `.env` 에서 `POSTGRES_HOST=127.0.0.1` 을 지정해두면 compose 가 그 값을 주입하여 컨테이너 안에서 자기 자신(컨테이너 loopback)으로 접속을 시도 → 연결 거부 발생. 이를 방지하기 위해 compose 파일에서는 `POSTGRES_HOST: db` 로 고정했습니다. 호스트에서 직접 실행(컨테이너 미사용) 할 때만 127.0.0.1 사용하세요.

### 3.1 Dev vs Prod Modes
| Mode | Command | Features |
|------|---------|----------|
| Development (hot reload) | `docker compose -f docker-compose.dev.yml up -d` | Bind mount source, `--reload`, quick iteration |
| Production snapshot | `docker compose -f docker-compose.yml up -d` | Immutable code image, no reload |

Note: In dev mode the Postgres service is exposed on host port 55432 (mapped to container 5432) to avoid collision with any locally running or prod-mapped Postgres on 5432.

Makefile shortcuts (added):
```bash
make docker-up-dev      # dev stack
make docker-down-dev
make docker-up-prod     # prod stack
make docker-down-prod
```

If `make` is not installed (e.g. minimal Windows environment), use helper scripts instead:
```bash
./scripts/docker-up-dev.sh
./scripts/docker-down-dev.sh
./scripts/docker-up-prod.sh
./scripts/docker-down-prod.sh
```
### 3.2 Dev Mode Troubleshooting
| Symptom | Likely Cause | Action |
|---------|--------------|--------|
| Prod는 되는데 Dev는 안 뜸 | 이름/볼륨 충돌 또는 캐시 | Run `docker compose -f docker-compose.dev.yml down -v && docker compose -f docker-compose.dev.yml up -d --build` |
| 코드 변경 반영 안 됨 | bind mount 실패 | Check `docker compose -f docker-compose.dev.yml exec app ls /app/backend` |
| reload 너무 잦음 | 대량 파일 감시 | Narrow `--reload-dir` or exclude large dirs |
| DB 데이터 dev/prod 섞임 | 동일 볼륨 이름 사용 | Dev file now uses `db_data_dev` volume |

Dev helper script sets `COMPOSE_PROJECT_NAME=devstack` so dev containers/volumes are isolated from prod. Remove that export if you prefer default naming.

### 3.3 FAST_STARTUP (Accelerated Dev Boot)
Dev compose sets `FAST_STARTUP=true`. In this mode only minimal components start initially:
- Always: HTTP API, risk engine load (if DB), risk metrics loop, inference log queue
- Skipped (if corresponding feature flag on): feature scheduler, ingestion consumer, auto retrain loop, calibration monitor, auto labeler

Benefits: faster cold start + fewer early reload race conditions during code editing.

Visibility:
```bash
curl http://localhost:8000/health/ready | jq
# You'll see reasons entries like "skipped:feature_scheduler"
```

Upgrade to full mode (start all skipped components) without restarting the container:
```bash
curl -X POST http://localhost:8000/admin/fast_startup/upgrade | jq
```
After upgrade, `skipped:*` reasons should disappear and `fast_startup` flag is cleared once all components are active.

Disable FAST_STARTUP entirely by setting `FAST_STARTUP=false` (edit `docker-compose.dev.yml` or override at runtime).

Optional: If you want the feature scheduler immediately, just set `FAST_STARTUP=false` and recreate the container.


Hot reload 반영 안 될 때 확인:
1. dev 파일을 사용했는지 (`docker-compose.dev.yml`)
2. logs 에 "Uvicorn running" 재출력되는지
3. 수정한 파일이 `backend/` 경로 아래인지


## 4. Running Alembic Migrations Inside the Container
If migrations aren't auto-run yet (we're not auto-running them here), exec into the app:
```bash
docker compose exec app bash
# Inside container
alembic upgrade head
```
(Adjust if your project has a helper script.)

## 5. Rebuilding After Code Changes
```bash
docker compose build app
docker compose up -d
```
Or use `--build` flag:
```bash
docker compose up --build app
```

### 5.1 Live Reload (Development Mode)
이미지 재빌드 없이 로컬 소스 수정 즉시 반영하고 싶다면 `docker-compose.override.yml` 를 사용합니다. 이 파일은 기본 compose 와 자동으로 병합되며, `backend/` 디렉터리를 컨테이너에 바인드 마운트하고 Uvicorn `--reload` 옵션을 켭니다.

사용 방법:
```bash
# (이미 override 파일이 repo 에 존재) 그냥 올리면 됩니다.
docker compose up -d

# 로그 확인
docker compose logs -f app

# 코드 수정 → 저장 → 몇 초 내 자동 재기동/리로드
```

확인 포인트:
- CPU 과다 사용 시: 변경 잦을 경우 reload 빈도 조절 (디렉터리 축소, 또는 --reload-dir 지정 조정)
- 의존성(pyproject) 변경 시: 컨테이너 내부에서 다시 `poetry install` 필요 (override 는 pyproject.toml 을 read-only 마운트)

프로덕션에서는 override 파일을 사용하지 않고, 기본 `docker-compose.yml` 만으로 빌드된 이미지를 실행하세요.

## 6. Stopping & Cleaning
```bash
docker compose down          # stop
docker compose down -v       # stop and remove volume (fresh DB)
```

## 7. Debugging asyncpg Pool Issues
Checklist:
1. Confirm the DB service is healthy: `docker compose ps` (State should be `healthy`).
2. Inspect app logs: `docker compose logs app | grep -i pool`.
3. Use admin endpoints:
   - `GET /admin/db/status` – Shows pool state, last errors.
   - `POST /admin/db/retry` – Forces a new attempt to create the pool.
4. If single connections succeed but pool fails, capture the first stack trace from logs.
5. Compare behavior between Windows host and container (Linux) to isolate OS/socket issues.

## 8. Environment Tweaks
To change credentials or feature flags, either edit `docker-compose.yml` or (recommended) parameterize with `${VAR}` and create `.env.docker`:
```bash
cp .env.docker.example .env.docker
# edit values
```
Then reference them in compose like:
```yaml
environment:
  POSTGRES_USER: ${POSTGRES_USER}
```
(Current file uses inline literals for simplicity.)

## 9. Optional: TimescaleDB
If you later need TimescaleDB, swap the `db` image:
```yaml
image: timescale/timescaledb-ha:pg15-latest
```
And ensure any extension creation remains guarded (already handled in migrations scripts).

## 10. Makefile Targets (Planned)
You can add shortcuts:
```makefile
docker-up: ## Start stack
docker compose up -d

docker-down: ## Stop stack
docker compose down

docker-logs: ## Tail app logs
docker compose logs -f app

docker-rebuild: ## Rebuild only app image
docker compose build app
```
(If you want, request and they will be added.)

## 11. Troubleshooting
| Symptom | Action |
|---------|--------|
| `Connection refused` during startup | Ensure `depends_on.condition: service_healthy` present (already). |
| Pool stuck in retry loop | Use `/admin/db/status` to see last error; run `/admin/db/retry`. |
| Migrations fail on Timescale extension | Verify guarded logic; ensure image actually has Timescale if required. |
| Container restarts repeatedly | Check logs for fatal import/poetry issues; run `docker compose logs app`. |
| Auth errors after changing DB password | You likely reused old volume. Run `docker compose down -v` to recreate cluster with new creds. |
| Need different host ports for parallel stacks | Set `DB_EXTERNAL_PORT`, `DEV_DB_EXTERNAL_PORT`, and/or `APP_PORT` in env. |

## 13. Database Reset / Reinitialize

Full wipe & fresh migrations:
```bash
# Dev
./scripts/db-reset-dev.sh

# Prod snapshot
./scripts/db-reset-prod.sh
```

Manual (dangerous) psql method inside db container:
```bash
docker compose -f docker-compose.dev.yml exec db psql -U postgres -d wooyoung -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres;"
docker compose -f docker-compose.dev.yml exec db psql -U postgres -d mywooyoungdata -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"  # example optional
```
Then rerun migrations:
```bash
docker compose -f docker-compose.dev.yml run --rm app poetry run alembic upgrade head
```

If Timescale or other extensions are optional, guarded migration scripts will skip gracefully.

## 12. Next Steps
- Decide whether to migrate runtime driver to async psycopg if pool failures persist.
- Add automated migration step in entrypoint (Makefile or script) if desired.
- Introduce integration tests that spin up this compose stack in CI.

---
Feel free to ask to extend this setup (e.g., add Prometheus, PgAdmin, or a migration init container).

## 14. Readiness Poll Helper
Use the helper script to wait until the service is ready (useful in automation or after a fresh build):
```bash
./scripts/dev-health.sh            # defaults to localhost:8000
./scripts/dev-health.sh localhost:8000 60 1  # host:port attempts interval
```
If it times out it prints the last response.

## 15. One-Command Dev Orchestration
새 스크립트 `scripts/dev-up-full.sh` 로 빌드 → DB → 마이그레이션 → 앱 → (옵션) FAST_STARTUP 업그레이드까지 한 번에 실행:
```
./scripts/dev-up-full.sh           # 기본 (빌드 + db + migrate + app + fast upgrade)
./scripts/dev-up-full.sh --reset   # 볼륨 초기화 포함
./scripts/dev-up-full.sh --no-build
./scripts/dev-up-full.sh --no-upgrade
```
옵션 요약:
| 옵션 | 설명 |
|------|------|
| --reset | `down -v` 후 깨끗하게 시작 |
| --no-build | 이미지 재빌드 생략 |
| --no-upgrade | FAST_STARTUP 모드 유지 (백그라운드 작업 미가동) |
| --upgrade | 강제로 업그레이드 (기본값) |

## 16. Hot Reload 문제 해결
Windows / 일부 파일시스템에서 inotify 계열 감지가 동작하지 않아 코드 저장이 반영 안 될 수 있습니다. 적용한 대응:
1. `WATCHFILES_FORCE_POLLING=1` 로 polling 모드 강제.
2. `--reload-dir /app/backend` 절대경로 지정 (상대 경로 매칭 오류 방지).

문제 지속 시 체크리스트:
```
docker compose -f docker-compose.dev.yml exec app bash -c 'echo test >> /app/backend/__reload_probe__ && ls -l /app/backend/__reload_probe__'
docker compose -f docker-compose.dev.yml logs -f app | grep -i 'Reloading' -n
```
보이지 않으면:
- 컨테이너 시간/호스트 시간 차이 (극단적 clock skew) 여부
- 파일 저장 시 편집기가 임시파일 -> rename 패턴 사용 여부 (VSCode 일반적으로 OK)
- 대용량 디렉토리 과도 감시 → `--reload-dir` 추가 세분화 또는 서브모듈 제외

그래도 안 되면 임시로 uvicorn 옵션에 `--reload-exclude` 로 noisy 폴더 제외, 혹은 polling interval 늘리기 (환경변수 `WATCHFILES_POLL_DELAY=1.0`). 요청 시 추가 구성 가능.

## 17. "Empty reply from server" (curl 52) / 127.0.0.1 바인딩 이슈
증상:
```
curl: (52) Empty reply from server
로그: Uvicorn running on http://127.0.0.1:8000
```
원인:
dev compose 에서 `sh -c "..."` 래퍼를 통해 uvicorn 실행 시 reload 워커가 host 인자를 잃고 기본 loopback(127.0.0.1) 으로 재기동하거나, 초기 워커가 비정상 종료하면서 커넥션이 RST 처리.

해결 조치:
1. exec(list) 또는 전용 스크립트 (`scripts/run-dev.sh`) 사용 → shell wrapping 최소화.
2. `--host 0.0.0.0` 강제.
3. 필요 시 reload 임시 비활성: `DEV_RELOAD=0` 환경변수 설정 후 재시작.

현재 구성:
```
docker-compose.dev.yml -> scripts/run-dev.sh 호출
scripts/run-dev.sh -> DEV_RELOAD=1 일 때만 --reload 옵션 활성
```
확인 절차:
```
docker compose -f docker-compose.dev.yml logs --tail=30 app | grep 'run-dev'
curl -v http://localhost:8000/healthz
```
출력에 `Uvicorn running on http://0.0.0.0:8000` 가 보여야 하며 Empty reply 가 재현되지 않아야 함.

문제 재발 시:
```
docker compose -f docker-compose.dev.yml down
DEV_RELOAD=0 docker compose -f docker-compose.dev.yml up -d --build app
```
reload 없이 정상인지 먼저 확인 후, reload 기능에 한정된 문제인지 분리.

## 18. run-dev.sh 사용 방법
스크립트 환경변수:
| 변수 | 의미 | 기본 |
|------|------|------|
| DEV_RELOAD | 1이면 --reload 활성 | 1 |
| UVICORN_HOST | Host 바인딩 | 0.0.0.0 |
| UVICORN_PORT | 포트 | 8000 |
| UVICORN_EXTRA_OPTS | 추가 uvicorn 플래그 | (없음) |

예시 (reload 끄기 + debug 로그):
```
DEV_RELOAD=0 UVICORN_EXTRA_OPTS="--log-level debug" docker compose -f docker-compose.dev.yml up -d --build app
```

스크립트는 시작 시 `[run-dev] Starting uvicorn ...` 프린트 → 문제시 이 라인 여부로 실행경로 확인.

## 19. Frontend (Vue Ingestion Dashboard)
간단한 Vue (Vite) 프론트엔드가 `/api/ingestion/status` 를 5초 간격으로 폴링하여 수집 상태를 보여줍니다.

### Dev 실행
```
docker compose -f docker-compose.dev.yml up -d --build frontend
```
브라우저: http://localhost:5173

프록시 설정: `vite.config.ts` 가 `/api` 경로를 백엔드 (기본 http://localhost:8000) 로 전달.
컨테이너 내부에서는 `VITE_BACKEND_URL=http://app:8000` 환경변수를 사용 (compose 설정).

### 수집 상태 JSON 예시
```
{
  "enabled": true,
  "running": true,
  "symbol": "xprusdt",
  "interval": "1m",
  "buffer_size": 3,
  "total_messages": 1524,
  "total_closed": 254,
  "last_message_ts": 1727741234.12,
  "last_closed_kline_close_time": 1727741220000
}
```
`last_message_ts` 는 epoch seconds, lag 는 프론트에서 현재 시각과 차이로 계산.

### Prod 빌드 (선택)
```
docker build -t xrp-frontend:prod ./frontend
docker run -p 8080:80 xrp-frontend:prod
```
혹은 별도 prod compose 서비스 추가 가능 (요청 시 추가).

### CORS
백엔드에 `CORSMiddleware` 추가되어 `http://localhost:5173` 등 기본 dev 오리진 허용.

### 커스터마이즈 아이디어
- 에러/재연결 횟수 지표 추가 (KlineConsumer 확장)
- 최근 n개 kline 테이블 뷰
- Prometheus 지표 시각화 (간단 sparkline)


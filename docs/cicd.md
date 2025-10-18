# CI/CD (GitHub Actions → Docker → GHCR → Deploy)

본 레포는 push 시 자동으로 Docker 이미지를 빌드하여 GHCR에 푸시하고, main 브랜치에서는 서버에 자동 배포, develop 브랜치에서는 스테이징 배포를 수행합니다.

## 개요
- 워크플로우: `.github/workflows/ci-cd.yml`
  - 이미지 빌드/푸시: backend, frontend → GHCR
  - 태그: `sha-<short>` + (main: `latest`, develop: `dev-latest`)
  - 프로덕션 배포(main): 서버 SSH 접속 → `docker compose -f docker-compose.prod.yml pull && up -d`
  - 스테이징 배포(develop): 서버 SSH 접속 → `docker compose -f docker-compose.dev.deploy.yml pull && up -d`
  - 배포 후 헬스체크: 백엔드 `/healthz`(200), 프론트 `/health.txt`(200)
    - 헬스체크 실패 시 워크플로우 실패 처리(fail-fast)
    - 배포 잡 동시성 제어(concurrency):
      - prod: 동시에 여러 배포가 겹치지 않도록 보장
      - staging: 진행 중 배포가 있을 때 새 배포가 오면 이전 배포 취소 후 최신만 유지
  - 보호된 환경(Environment) 사용:
    - production, staging 환경에 연결되어 환경별 승인/시크릿 분리 가능

    ## 자동 롤백(Prev 태그)

    배포 전에 기존 태그를 보존합니다.

    - main(프로덕션):
      - `latest` → `prev` 로 보존
      - 새 이미지 푸시 후 배포, 헬스체크 실패 시 `BACKEND_TAG=prev`, `FRONTEND_TAG=prev`로 자동 롤백 재배포
    - develop(스테이징):
      - `dev-latest` → `dev-prev` 로 보존
      - 실패 시 `BACKEND_TAG=dev-prev`, `FRONTEND_TAG=dev-prev`로 롤백

    수동 롤백이 필요하면 서버에서 아래 스크립트를 사용할 수 있습니다.

    ```bash
    # 예: 특정 sha 태그로 롤백
    BACKEND_TAG=sha-abc1234 FRONTEND_TAG=sha-abc1234 GH_OWNER=<owner> DEPLOY_PATH=~/pro11 ./scripts/rollback_prod.sh
    ```

## 사전 준비
GitHub Secrets (레포 설정 → Secrets and variables → Actions)
- 프로덕션 배포용:
  - `DEPLOY_HOST`: 서버 호스트/IP
  - `DEPLOY_USER`: SSH 사용자
  - `DEPLOY_KEY`: SSH 개인키 (PEM)
  - 선택: `GHCR_USERNAME`, `GHCR_TOKEN` (프라이빗 GHCR일 경우 서버 측 로그인용)
- 스테이징 배포용(옵션):
  - `STAGING_HOST`, `STAGING_USER`, `STAGING_KEY`
 - 알림(옵션):
   - `SLACK_WEBHOOK`: Slack 인커밍 웹훅 URL (설정 시 배포 시작/성공/실패 알림 전송)

서버 준비
- Docker, Docker Compose 설치
- 레포 클론 위치(예: `~/pro11`)에 `docker-compose.prod.yml`, `docker-compose.dev.deploy.yml`, `.env`, `.env.private` 준비
- 프로덕션 포트: 백엔드 8000, 프론트 8080 (컴포즈 파일 기준)
- 스테이징 포트: 백엔드 8000, 프론트 5173 (dev deploy 컴포즈 기준)

## 서버 컴포즈 파일
프로덕션: `docker-compose.prod.yml` (이미지는 GHCR latest)

```yaml
services:
  app:
    image: ghcr.io/<owner>/pro11-backend:latest
    env_file:
      - .env
      - .env.private
    ports:
      - "8000:8000"
  frontend:
    image: ghcr.io/<owner>/pro11-frontend:latest
    ports:
      - "8080:80"
```

스테이징: `docker-compose.dev.deploy.yml` (이미지는 GHCR `dev-latest`)

```yaml
services:
  app:
    image: ghcr.io/<owner>/pro11-backend:${BACKEND_TAG:-dev-latest}
  frontend:
    image: ghcr.io/<owner>/pro11-frontend:${FRONTEND_TAG:-dev-latest}
```

## 수동 배포
서버에서 수동으로 배포하려면 다음 스크립트를 사용할 수 있습니다.

```bash
GH_OWNER=<owner> DEPLOY_PATH=~/pro11 ./scripts/deploy_prod.sh
```

## 알림 (Slack)

`SLACK_WEBHOOK`을 설정하면 배포 시작과 결과(성공/실패)를 간단 텍스트 메시지로 통지합니다.
메시지에는 브랜치, 커밋 SHA(앞 7자리), GitHub Actions 실행 URL이 포함됩니다.

## 수동 배포(워크플로우에서)

GitHub Actions의 이 워크플로우는 수동 실행(workflow_dispatch)으로도 배포할 수 있습니다.

- 입력값
  - target: prod | staging (기본 prod)
  - backend_tag: (옵션) 예: latest, dev-latest, sha-xxxxxxx, prev/dev-prev
  - frontend_tag: (옵션) 예: latest, dev-latest, sha-xxxxxxx, prev/dev-prev
  - gh_owner: (옵션) GHCR 네임스페이스(기본: repo owner)
  - deploy_path: (옵션) 서버 경로(기본: $HOME/pro11)

수동 실행 시 입력값으로 지정한 태그를 사용해 대상 환경에 배포합니다. 보호된 환경을 사용 중이면 해당 환경의 승인 절차를 거친 뒤 실행됩니다.

## 참고 사항
- 서버에서 리포지토리 파일 접근이 불가하면 최초 1회 scp로 내려받거나 `git clone` 하세요.
- 프라이빗 GHCR은 서버에서 수동 로그인 가능: `echo <TOKEN> | docker login ghcr.io -u <USER> --password-stdin`.
- 필요 시 multi-arch 빌드, 캐시 정책, 태그 전략을 조정하세요.

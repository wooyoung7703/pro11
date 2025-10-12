# Lightweight production Dockerfile for XRP Trading System API
# Multi-stage build to reduce final image size

FROM python:3.11-slim AS base

# Allow overriding at build time: docker build --build-arg POETRY_VERSION=2.2.1 .
ARG POETRY_VERSION=2.2.1
ARG DEV_DEPS=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    POETRY_VERSION=${POETRY_VERSION} \
    PATH="/home/appuser/.local/bin:$PATH" \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"

WORKDIR /app

# 캐시 최적화를 위해 pyproject만 먼저 복사
COPY pyproject.toml ./
# 잠재적 lock 파일도 복사 (있으면 캐시 활용)
COPY poetry.lock* ./

# 선택적 dev 설치 (ARG DEV_DEPS=true 시 dev 그룹 포함)
RUN poetry config virtualenvs.in-project true && \
        if [ "$DEV_DEPS" = "true" ]; then \
            poetry install --no-interaction --no-ansi --with dev --no-root; \
        else \
            poetry install --no-interaction --no-ansi --only main --no-root; \
        fi

# 소스 복사 (패키지 설치 위해 필요)
COPY backend ./backend
COPY README.md ./
COPY alembic.ini ./

# 루트 패키지 설치 (이미 의존성 캐시 후 빠르게 처리)
RUN if [ "$DEV_DEPS" = "true" ]; then \
            poetry install --no-interaction --no-ansi --with dev; \
        else \
            poetry install --no-interaction --no-ansi --only main; \
        fi

# 비루트 사용자 생성 (권한 최소화)
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["poetry", "run", "uvicorn", "backend.apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

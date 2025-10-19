#!/bin/bash
# 로컬 배포 스크립트

echo "🚀 로컬 배포 시작..."

# GitHub Container Registry 로그인 확인
echo "1️⃣ Docker 로그인 확인..."
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker가 실행되지 않았습니다. Docker Desktop을 시작해주세요."
    exit 1
fi

# GitHub Token 확인
if [ -z "$GITHUB_TOKEN" ]; then
    echo "⚠️  GITHUB_TOKEN 환경변수가 설정되지 않았습니다."
    echo "다음 명령어로 설정하거나 GitHub Personal Access Token을 입력하세요:"
    echo "export GITHUB_TOKEN=your_token_here"
    echo ""
    read -p "GitHub Personal Access Token 입력 (Enter로 건너뛰기): " token
    if [ -n "$token" ]; then
        export GITHUB_TOKEN=$token
    fi
fi

# GHCR 로그인
if [ -n "$GITHUB_TOKEN" ]; then
    echo "2️⃣ GitHub Container Registry 로그인..."
    echo "$GITHUB_TOKEN" | docker login ghcr.io -u wooyoung7703 --password-stdin
    if [ $? -eq 0 ]; then
        echo "✅ GHCR 로그인 성공"
    else
        echo "❌ GHCR 로그인 실패. 토큰을 확인해주세요."
    fi
else
    echo "⚠️  GITHUB_TOKEN이 없어 공개 이미지만 사용 가능합니다."
fi

# 환경변수 설정
export GHCR_USER="wooyoung7703"
export GH_OWNER="wooyoung7703"
export COMPOSE_PROJECT_NAME="wooyoung7703"

echo "3️⃣ 최신 이미지 가져오기..."
docker compose -f docker-compose.dev.deploy.yml pull

if [ $? -eq 0 ]; then
    echo "✅ 이미지 pull 성공"
else
    echo "⚠️  일부 이미지 pull 실패 (계속 진행)"
fi

if [ "${USE_LOCAL_COMPOSE:-0}" = "1" ]; then
    echo "4️⃣ (로컬) docker-compose.local.yml로 서비스 시작..."
    export COMPOSE_PROJECT_NAME=pro11local
    docker compose -f docker-compose.local.yml up -d --build
    if [ $? -ne 0 ]; then
        echo "❌ 로컬 서비스 시작 실패"
        exit 1
    fi
    echo "⏳ 백엔드 준비 대기 (/admin/features/status)"
    for i in $(seq 1 120); do
        if curl -sf -H "X-API-Key: ${API_KEY:-dev-key}" http://localhost:8010/admin/features/status > /dev/null; then
            echo "✅ 백엔드 준비 완료"
            break
        fi
        sleep 1
    done
    echo "📊 모델 요약 확인"
    curl -sS -H "X-API-Key: ${API_KEY:-dev-key}" 'http://localhost:8010/api/models/summary?name=bottom_predictor&limit=3' | jq -C . || true
else
    echo "4️⃣ 서비스 시작..."
    docker compose -f docker-compose.dev.deploy.yml up -d
fi

if [ $? -eq 0 ]; then
    echo "✅ 서비스 시작 성공"
    echo ""
    echo "🎉 배포 완료!"
    echo ""
    echo "📋 실행 중인 서비스:"
    if [ "${USE_LOCAL_COMPOSE:-0}" = "1" ]; then
        docker compose -f docker-compose.local.yml ps
    else
        docker compose -f docker-compose.dev.deploy.yml ps
    fi
    echo ""
    if [ "${USE_LOCAL_COMPOSE:-0}" = "1" ]; then
        echo "📝 로그 확인: docker compose -f docker-compose.local.yml logs -f"
        echo "🛑 중지: docker compose -f docker-compose.local.yml down"
    else
        echo "📝 로그 확인: docker compose -f docker-compose.dev.deploy.yml logs -f"
        echo "🛑 중지: docker compose -f docker-compose.dev.deploy.yml down"
    fi
else
    echo "❌ 서비스 시작 실패"
    if [ "${USE_LOCAL_COMPOSE:-0}" = "1" ]; then
        echo "로그 확인: docker compose -f docker-compose.local.yml logs"
    else
        echo "로그 확인: docker compose -f docker-compose.dev.deploy.yml logs"
    fi
    exit 1
fi

echo "5️⃣ 이전 이미지 정리..."
docker system prune -f

echo "✅ 로컬 배포 완료!"
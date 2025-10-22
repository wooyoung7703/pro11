#!/bin/bash
# Docker 환경 복구 스크립트

echo "🔧 Docker 환경 복구 시작..."

# 1. 현재 실행 중인 컨테이너 확인
echo "1️⃣ 현재 실행 중인 컨테이너:"
docker ps

echo ""
echo "2️⃣ 모든 컨테이너 (정지된 것 포함):"
docker ps -a

echo ""
echo "3️⃣ Docker 볼륨 확인:"
docker volume ls | grep pgdata

echo ""
echo "4️⃣ 현재 포트 사용 상황:"
netstat -tulpn 2>/dev/null | grep -E ':(5173|8000|55432|5432)' || echo "netstat 명령어 없음"

echo ""
echo "5️⃣ Docker Compose 서비스 상태 확인:"
docker compose -f docker-compose.dev.deploy.yml ps

echo ""
echo "🛠️  복구 작업 시작..."

# 6. 기존 컨테이너 정리 (필요시)
echo "6️⃣ 기존 컨테이너 정리..."
docker compose -f docker-compose.dev.deploy.yml down

# 7. 볼륨 상태 재확인
echo "7️⃣ 볼륨 상태 재확인:"
docker volume ls | grep pgdata

# 8. 서비스 재시작
echo "8️⃣ 서비스 재시작..."
docker compose -f docker-compose.dev.deploy.yml up -d

# 9. 최종 상태 확인
echo "9️⃣ 최종 상태 확인:"
sleep 5
docker compose -f docker-compose.dev.deploy.yml ps

echo ""
echo "🔍 접속 정보:"
echo "- 프론트엔드: http://localhost:5173"
echo "- 백엔드 API: http://localhost:8000"
echo "- 데이터베이스: localhost:55432"
echo ""
echo "✅ 복구 완료!"
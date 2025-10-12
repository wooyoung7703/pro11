#!/bin/bash
# SSH 키 자동 설정 스크립트

echo "🔧 GitHub Actions용 SSH 키 설정 시작..."

# 1. SSH 키 생성
echo "1️⃣ SSH 키 생성 중..."
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy -N ""

if [ $? -eq 0 ]; then
    echo "✅ SSH 키 생성 완료"
else
    echo "❌ SSH 키 생성 실패"
    exit 1
fi

# 2. 공개 키를 authorized_keys에 추가
echo "2️⃣ 공개 키 등록 중..."
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys

# 3. 권한 설정
echo "3️⃣ 권한 설정 중..."
chmod 600 ~/.ssh/authorized_keys ~/.ssh/github_deploy
chmod 700 ~/.ssh

echo "✅ SSH 키 설정 완료!"
echo ""
echo "🔑 GitHub Secret에 설정할 개인 키:"
echo "================================================"
cat ~/.ssh/github_deploy
echo "================================================"
echo ""
echo "📝 GitHub Secrets 설정 방법:"
echo "1. https://github.com/wooyoung7703/pro11/settings/secrets/actions 접속"
echo "2. 'New repository secret' 클릭"
echo "3. 다음 secrets 생성:"
echo "   - Name: DEV_DEPLOY_HOST, Value: $(curl -s ifconfig.me || hostname -I | awk '{print $1}')"
echo "   - Name: DEV_DEPLOY_USER, Value: $(whoami)"
echo "   - Name: DEV_DEPLOY_SSH_KEY, Value: 위에 출력된 전체 키 내용"
echo ""
echo "🧪 로컬 테스트:"
echo "ssh -i ~/.ssh/github_deploy $(whoami)@$(curl -s ifconfig.me || hostname -I | awk '{print $1}')"
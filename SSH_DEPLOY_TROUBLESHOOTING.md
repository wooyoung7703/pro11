# SSH 배포 오류 해결 가이드

## 현재 오류
```
ssh: handshake failed: ssh: unable to authenticate, attempted methods [none publickey], no supported methods remain
```

## 즉시 해결 방법

### 방법 1: SSH 키 재설정 (권장)

1. **서버에 접속하여 새로운 SSH 키 생성**
   ```bash
   # 새로운 SSH 키 생성
   ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy -N ""
   
   # 공개 키를 authorized_keys에 추가
   cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
   
   # 권한 설정
   chmod 600 ~/.ssh/authorized_keys ~/.ssh/github_deploy
   chmod 700 ~/.ssh
   
   # 개인 키 출력 (전체 복사)
   cat ~/.ssh/github_deploy
   ```

2. **GitHub Secrets 설정**
   - 리포지토리 → Settings → Secrets and variables → Actions
   - 다음 secrets 설정:
     - `DEV_DEPLOY_HOST`: 서버 IP 주소
     - `DEV_DEPLOY_USER`: SSH 사용자명 (ubuntu, ec2-user 등)
     - `DEV_DEPLOY_SSH_KEY`: 위에서 출력된 전체 개인 키

### 방법 2: 패스워드 인증 (임시 해결책)

1. **서버에서 패스워드 인증 활성화**
   ```bash
   sudo nano /etc/ssh/sshd_config
   ```
   
   다음 라인 수정:
   ```
   PasswordAuthentication yes
   ```
   
   SSH 서비스 재시작:
   ```bash
   sudo systemctl restart sshd
   ```

2. **GitHub Secret 추가**
   - `DEV_DEPLOY_PASSWORD`: 서버 사용자 패스워드

### 방법 3: 현재 워크플로우 테스트

현재 워크플로우는 SSH 키 실패시 자동으로 패스워드 인증으로 전환됩니다:

1. SSH 키 인증 시도
2. 실패시 패스워드 인증으로 fallback

## 필수 GitHub Secrets

**최소 요구사항:**
- `DEV_DEPLOY_HOST`: 서버 IP
- `DEV_DEPLOY_USER`: SSH 사용자명

**SSH 키 인증용:**
- `DEV_DEPLOY_SSH_KEY`: SSH 개인 키

**패스워드 인증용 (백업):**
- `DEV_DEPLOY_PASSWORD`: 서버 패스워드

## 연결 테스트

로컬에서 테스트:
```bash
# SSH 키로 테스트
ssh -i ~/.ssh/github_deploy username@server_ip

# 패스워드로 테스트  
ssh username@server_ip
```

## 일반적인 문제 해결

### SSH 키 형식 문제
```bash
# 올바른 키 형식 확인
head -1 ~/.ssh/github_deploy
# 출력: -----BEGIN OPENSSH PRIVATE KEY-----

tail -1 ~/.ssh/github_deploy  
# 출력: -----END OPENSSH PRIVATE KEY-----
```

### 서버 방화벽 문제
```bash
# SSH 포트 확인
sudo ufw status
sudo iptables -L | grep 22

# AWS 보안 그룹에서 포트 22 허용 확인
```

### 사용자명 확인
```bash
# 현재 사용자 확인
whoami

# 가능한 사용자들
# AWS EC2: ec2-user, ubuntu
# Ubuntu: ubuntu  
# CentOS: centos
# Debian: debian
```

## 성공 후 보안 강화

SSH 키 인증 성공 후:
```bash
# 패스워드 인증 비활성화
sudo nano /etc/ssh/sshd_config
# PasswordAuthentication no

sudo systemctl restart sshd
```

## 다음 단계

1. ✅ 위 방법 중 하나 선택
2. ✅ GitHub Secrets 설정
3. ✅ 워크플로우 재실행
4. ✅ 성공 확인
5. ✅ 보안 설정 강화
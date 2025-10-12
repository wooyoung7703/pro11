# SSH 배포 오류 해결 가이드

## 현재 오류
```
ssh: handshake failed: ssh: unable to authenticate, attempted methods [none publickey], no supported methods remain
```

## 문제 진단 및 해결

### 1. GitHub Secrets 확인 필수 사항

GitHub 리포지토리의 **Settings > Secrets and variables > Actions**에서 다음 secrets 확인:

- `DEV_DEPLOY_HOST`: 서버 IP 주소 (예: 123.456.789.012)
- `DEV_DEPLOY_USER`: SSH 사용자명 (예: ubuntu, ec2-user, root)
- `DEV_DEPLOY_SSH_KEY`: SSH 개인 키 전체 내용

### 2. 서버에서 SSH 키 새로 생성

서버에 접속하여:

```bash
# 1. 새로운 SSH 키 생성
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy -N ""

# 2. 공개 키를 authorized_keys에 추가
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys

# 3. 권한 설정
chmod 600 ~/.ssh/authorized_keys ~/.ssh/github_deploy
chmod 700 ~/.ssh

# 4. 개인 키 내용 확인 (이것을 GitHub Secret에 복사)
cat ~/.ssh/github_deploy
```

### 3. GitHub Secret 업데이트

위에서 출력된 개인 키 전체 내용을 `DEV_DEPLOY_SSH_KEY`에 복사
(-----BEGIN ... -----END 포함 전체)

### 4. 서버 SSH 설정 확인

`/etc/ssh/sshd_config` 파일에서:

```bash
sudo nano /etc/ssh/sshd_config
```

다음 설정 확인:
```
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
PasswordAuthentication no
```

설정 변경 후 SSH 재시작:
```bash
sudo systemctl restart sshd
```

### 5. 로컬에서 연결 테스트

```bash
ssh -i ~/.ssh/github_deploy username@server_ip
```

### 6. GitHub Actions 재실행

위 단계를 완료한 후 워크플로우를 다시 실행합니다.

## 일반적인 문제들

### A. SSH 키 형식 문제
- Windows에서 생성한 키의 줄바꿈 문제
- 키가 잘려서 복사된 경우
- 잘못된 키 형식 (공개키를 개인키 자리에 넣은 경우)

### B. 서버 권한 문제
```bash
# ~/.ssh 디렉토리 권한
chmod 700 ~/.ssh

# authorized_keys 파일 권한
chmod 600 ~/.ssh/authorized_keys

# 개인 키 파일 권한
chmod 600 ~/.ssh/github_deploy
```

### C. 사용자명 문제
- AWS EC2: `ec2-user` 또는 `ubuntu`
- Ubuntu: `ubuntu`
- CentOS: `centos`
- 일반 서버: 실제 사용자명 확인

### D. 호스트 문제
- IP 주소가 정확한지 확인
- 포트 22가 열려있는지 확인 (보안 그룹/방화벽)
- DNS 이름 대신 IP 주소 사용 권장

## 응급 해결책

SSH 키 문제가 계속 발생하면 임시로 패스워드 인증 사용:

1. 서버에서 패스워드 인증 활성화:
```bash
sudo nano /etc/ssh/sshd_config
# PasswordAuthentication yes
sudo systemctl restart sshd
```

2. 워크플로우에서 패스워드 사용:
```yaml
- name: Upload compose file to server
  uses: appleboy/scp-action@v0.1.7
  with:
    host: ${{ secrets.DEV_DEPLOY_HOST }}
    username: ${{ secrets.DEV_DEPLOY_USER }}
    password: ${{ secrets.DEV_DEPLOY_PASSWORD }}
    source: docker-compose.dev.deploy.yml
    target: ~/pro11/
```

## 다음 단계

1. ✅ SSH 키 새로 생성
2. ✅ GitHub Secret 업데이트  
3. ✅ 로컬에서 연결 테스트
4. ✅ 워크플로우 재실행
5. ✅ 성공 시 패스워드 인증 비활성화
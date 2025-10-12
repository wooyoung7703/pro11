# SSH 배포 문제 해결 가이드

## 현재 발생한 오류
```
ssh: handshake failed: ssh: unable to authenticate, attempted methods [none publickey], no supported methods remain
```

## 해결 방법

### 1. GitHub Secrets 확인
리포지토리의 Settings > Secrets and variables > Actions에서 확인:

- `DEV_DEPLOY_HOST`: 서버 IP 또는 도메인명
- `DEV_DEPLOY_USER`: SSH 접속 사용자명 (예: ubuntu, ec2-user)
- `DEV_DEPLOY_SSH_KEY`: SSH 개인 키 전체 내용

### 2. SSH 키 재생성 (필요시)

서버에서 새로운 SSH 키 쌍 생성:
```bash
ssh-keygen -t rsa -b 4096 -C "deploy@pro11" -f ~/.ssh/deploy_key
```

공개 키를 authorized_keys에 추가:
```bash
cat ~/.ssh/deploy_key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
chmod 700 ~/.ssh
```

개인 키 내용을 GitHub Secret에 저장:
```bash
cat ~/.ssh/deploy_key
```

### 3. 서버 SSH 설정 확인

`/etc/ssh/sshd_config` 파일에서 다음 설정 확인:
```
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
PasswordAuthentication no  # 보안상 권장
```

설정 변경 후 SSH 서비스 재시작:
```bash
sudo systemctl restart sshd
```

### 4. 로컬에서 SSH 연결 테스트

```bash
ssh -i /path/to/private_key username@server_ip
```

### 5. 워크플로우 디버깅

워크플로우에 디버그 모드 추가하여 더 자세한 로그 확인:
```yaml
- name: Upload compose file to server
  uses: appleboy/scp-action@v0.1.7
  with:
    host: ${{ secrets.DEV_DEPLOY_HOST }}
    username: ${{ secrets.DEV_DEPLOY_USER }}
    key: ${{ secrets.DEV_DEPLOY_SSH_KEY }}
    debug: true  # 디버그 모드 활성화
    source: docker-compose.dev.deploy.yml
    target: ~/pro11/
```

### 6. 대안: 패스워드 인증 (임시)

보안상 권장하지 않지만, 임시로 패스워드 인증을 사용할 수 있습니다:
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

## 일반적인 원인들

1. **SSH 키 형식 오류**: Windows에서 생성된 키나 잘못된 줄바꿈
2. **권한 문제**: ~/.ssh 디렉토리나 authorized_keys 파일의 권한이 잘못됨
3. **사용자명 오류**: 잘못된 SSH 사용자명 사용
4. **서버 설정**: SSH 서버에서 공개키 인증이 비활성화됨
5. **네트워크 문제**: 방화벽이나 보안 그룹에서 SSH 포트(22) 차단

## 다음 단계

1. GitHub Secrets 재확인
2. SSH 키 재생성 및 설정
3. 서버 SSH 설정 점검
4. 로컬에서 SSH 연결 테스트
5. 워크플로우 재실행
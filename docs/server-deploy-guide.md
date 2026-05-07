# Ubuntu 서버 배포 가이드

> [`bongho/trading-system-public`](https://github.com/bongho/trading-system-public) 기준

---

## 사전 요구사항

- Ubuntu 서버 (8GB RAM 이상)
- Docker + Docker Compose 설치됨
- GitHub 저장소 접근 권한 (Public 레포는 SSH 인증 불필요, HTTPS clone 가능)

---

## 1단계: GitHub SSH 키 설정 (최초 1회)

서버에서 저장소를 clone하려면 SSH 인증이 필요합니다.

```bash
# 서버에서 SSH 키 생성
ssh-keygen -t ed25519 -C "your-server@trading" -f ~/.ssh/github_trading -N ""

# 공개키 출력 — 이 내용을 GitHub에 등록
cat ~/.ssh/github_trading.pub
```

출력된 공개키를 복사한 뒤:
1. GitHub → [Settings → SSH and GPG keys](https://github.com/settings/keys)
2. **New SSH key** → 제목: `trading-server`, 키 붙여넣기 → **Add SSH key**

```bash
# SSH 설정 파일에 추가
cat >> ~/.ssh/config << 'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_trading
  IdentitiesOnly yes
EOF

chmod 600 ~/.ssh/config

# 연결 테스트
ssh -T git@github.com
# 성공 시: "Hi <your-github-username>! You've successfully authenticated..."
```

---

## 2단계: 코드 Clone

```bash
# 홈 디렉토리에 클론
cd ~
git clone https://github.com/bongho/trading-system-public.git trading-system
cd trading-system
```

---

## 3단계: .env 설정

```bash
cp .env.example .env
nano .env
```

아래 항목을 채워넣으세요:

```env
# ── 업비트 ──────────────────────────────────────
UPBIT_ACCESS_KEY=your_access_key
UPBIT_SECRET_KEY=your_secret_key

# ── 텔레그램 ─────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# ── AI 백엔드 (OpenAI 권장) ───────────────────────
OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...   # 선택 사항

# ── 운영 설정 ─────────────────────────────────────
DRY_RUN=true          # 첫 배포는 true로 시작, 실매매 시 false
DB_PATH=data/trading.db

# ── AI 에이전트 활성화 ────────────────────────────
AI_ENABLED=true
AI_BACKEND=openai     # openai 또는 claude
AI_MODEL=gpt-4o
```

---

## 4단계: 배포

```bash
chmod +x deploy.sh
./deploy.sh deploy
```

정상 실행 시 아래와 같이 출력됩니다:
```
✅ 배포 완료
   로그 확인: ./deploy.sh logs
   상태 확인: ./deploy.sh status
```

---

## 5단계: 동작 확인

```bash
# 실시간 로그
./deploy.sh logs

# 컨테이너 상태 + 메모리 사용량
./deploy.sh status
```

텔레그램에서:
```
/status   — 봇 연결 확인
/balance  — 잔고 조회 (업비트)
```

---

## 코드 업데이트 방법

로컬에서 commit + push 후 서버에서:

```bash
cd ~/trading-system
./deploy.sh pull
```

내부적으로 `git pull → docker compose build → docker compose up -d` 를 순서대로 실행합니다.

---

## 자주 쓰는 명령 정리

| 명령 | 설명 |
|------|------|
| `./deploy.sh deploy` | 최초 빌드 + 실행 |
| `./deploy.sh pull` | 코드 업데이트 + 재빌드 |
| `./deploy.sh logs` | 실시간 로그 |
| `./deploy.sh status` | 상태 + 리소스 사용량 |
| `./deploy.sh restart` | 컨테이너 재시작 (코드 변경 없을 때) |
| `./deploy.sh stop` | 중지 |

---

## 리소스 설정 (docker-compose.yml)

현재 설정: 컨테이너당 **600MB RAM / CPU 1코어** (8GB 서버 기준 적합)

더 줄이고 싶으면 `docker-compose.yml`에서 조정:
```yaml
mem_limit: 400m      # 최소 400MB 권장
cpus: "0.5"          # CPU 0.5코어
```

---

## 문제 해결

### 컨테이너가 계속 재시작될 때
```bash
docker logs trading-engine --tail=50
```

### .env 키를 바꿨을 때
```bash
./deploy.sh restart   # 재빌드 없이 환경변수만 반영
```

### 전략 파일을 서버에서 직접 수정했을 때
`src/strategies/` 는 볼륨 마운트되어 있으므로 **재시작 없이 즉시 반영**됩니다.
텔레그램에서 `/strategy reload` 로 확인하세요.

### 디스크 정리
```bash
docker system prune -f   # 사용하지 않는 이미지/컨테이너 삭제
```

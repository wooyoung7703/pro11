# Dev setup and safety

## 1. Use safe env profiles
- Copy `.env.safety` to `.env` for local runs (no real trading, testnet on, mock exchange).
- Never commit `.env` or real keys. Only `.env.example` is tracked.
- For real secrets on your machine, create `.env.private` from `.env.private.example` and fill values locally. This file is ignored by Git.
- Loading order suggestion: load `.env` first, then merge `.env.private` if present, so safe defaults remain while private overrides apply only on your box.

## 2. Enable pre-commit secret scan
- Configure Git to use local hooks:
  - `git config core.hooksPath .githooks`
- Make the hook executable on Unix shells (Git Bash on Windows is fine):
  - `chmod +x .githooks/pre-commit`

The hook blocks:
- Any attempt to commit `.env` / `.env.*`
- Staged changes containing common secret patterns (API keys, secrets)

### 2.1 Quick verify (optional)
If your workspace is a Git repo, you can sanity-check the hook locally:

- Ensure hooks are active:
  - `git config core.hooksPath .githooks`
- Try to stage a dummy long token and commit (should BLOCK):
  - Create a file with a long token-like string (e.g., `echo "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" > dummy.txt`)
  - `git add dummy.txt && git commit -m "test token"` → expect a BLOCK message
- Try to commit `.env` (should BLOCK):
  - `git add .env && git commit -m "should block"` → expect a BLOCK message

Note: If this folder is not yet a Git repo, you may initialize one with `git init` (optional). Do this only if it fits your workflow.

## 3. Key rotation reminder
- If a secret was committed previously, rotate it immediately and purge from history if necessary.

## 4. Next steps
- Backend SSE/WebSocket signals stream (snapshot+delta, seq/epoch, heartbeat)
- Frontend realtime store consumer and connection status badges
 - Optional: Add a tiny loader in your app startup to read `.env.private` after `.env` (if your framework doesn't do this automatically).

## 5. Frontend SSE tuning (Vite env)
The frontend exposes a few Vite env knobs to tune realtime "freshness" and lag severity without code changes.

- File: `frontend/.env.example` contains documented defaults. Copy entries to your `frontend/.env` or `frontend/.env.local` when needed.

- Variables:
  - `VITE_SIG_FRESH_MS` (default 20000): Signals SSE freshness threshold in ms.
  - `VITE_RISK_FRESH_MS` (default 15000): Risk SSE freshness threshold in ms.
  - `VITE_SEVERE_LAG_MULT` (default 3): Severe lag when age > fresh_ms * multiplier.

- Tips:
  - Staging with bursty networks: relax thresholds slightly (e.g., +5–10s) to avoid noisy warnings.
  - Low-latency prod: tighten thresholds to surface real lag quickly.
  - Values are strings in env, code parses them as numbers; invalid values fallback to defaults.

## 6. Bottom 모델 자동화 설정 (학습/추론/프로모션)

아래 환경변수를 설정하면 자동 재학습/프로모션/캘리브레이션 모니터가 저점(bottom) 타겟에 맞춰 동작합니다.

- 필수 스위치
  - `TRAINING_TARGET_TYPE=bottom`
  - `AUTO_PROMOTE_MODEL_NAME=bottom_predictor`
  - `AUTO_RETRAIN_ENABLED=true`
  - `AUTO_PROMOTE_ENABLED=true`
  - `CALIBRATION_MONITOR_ENABLED=true`
  - `CALIBRATION_RETRAIN_ENABLED=true`

- 레이블 파라미터(기본값 있음, 필요시 조정)
  - `BOTTOM_LOOKAHEAD` (기본 30)
  - `BOTTOM_DRAWDOWN` (기본 0.005)
  - `BOTTOM_REBOUND` (기본 0.003)

동작 요약:
- 자동 추론 루프가 bottom 모델로 추론하고 로그에 `extra.target=bottom`을 기록합니다.
- 캘리브레이션 모니터는 bottom 로그만 필터링하여 ECE/Brier를 계산하고, 프로덕션(bottom) 대비 드리프트를 감지합니다.
- 드리프트 또는 캘리브레이션 추천 시 자동 재학습이 `run_training_bottom`을 호출해 bottom 모델을 학습합니다.
- 자동 프로모션은 신규 bottom 모델을 현재 bottom 프로덕션과 비교하여 기준 충족 시 승격합니다.

빠른 점검:
- `GET /api/models/summary?name=bottom_predictor&model_type=supervised`
- `GET /api/training/auto/status`
- `GET /api/inference/calibration/live?target=bottom`


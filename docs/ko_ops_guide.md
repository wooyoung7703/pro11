# 운영 가이드 (Korean Ops Guide)

이 문서는 Trading/Calibration/Admin 화면과 관련 환경변수를 한국어로 정리한 운영 가이드입니다.

## 1) Trading 화면 개요

## 2) Calibration & 모니터링

### Calibration Bins 세부

### 캘리브레이션 드리프트/재학습 추천 운영 가이드

증상: 라이브 확률 보정(캘리브레이션)이 프로덕션 대비 악화되어 ΔECE가 커지고(예: 0.46), 절대/상대 편차 연속치가 길게 이어져 재학습 추천이 빈번하게 발생.

바로 완화(권장 파라미터)
- 아래 값을 `.env`에 설정 후 백엔드 재시작으로 즉시 추천 빈도 완화가 가능합니다.
  - CALIBRATION_MONITOR_ECE_DRIFT_ABS=0.10
  - CALIBRATION_MONITOR_ECE_DRIFT_REL=0.8
  - CALIBRATION_MONITOR_ABS_STREAK_TRIGGER=5
  - CALIBRATION_MONITOR_REL_STREAK_TRIGGER=5
  - CALIBRATION_MONITOR_WINDOW_SECONDS=21600  # 6시간
  - CALIBRATION_ABS_DELTA_MULTIPLIER=2.5      # ΔECE가 절대임계 2.5배 이상 시 강한 드리프트로 간주
  - CALIBRATION_RECOMMEND_COOLDOWN_SECONDS=21600  # 추천 쿨다운 6시간

원인 체크리스트(근본 해결)
- 라벨/정렬: 심볼·인터벌·close_time 정렬, 라벨 지연/누락/중복 여부
- prior 쉬프트: 최근 24~48h 사건 빈도 급변 여부(Prod vs Live)
- 피처 정합성: 학습 vs 서빙 전처리·결측·클리핑·윈도우 스펙 일치 여부
- 성능 곡선: Live vs Prod reliability curve/히스토그램으로 특정 구간 붕괴인지 전구간인지 확인
- 데이터 품질: 샘플 수 급증/급감, 로그 중복/누락 여부

운영 절차(순차)
1) 즉시 완화값 적용: 위 권장 파라미터를 `.env`에 반영 후 백엔드 재시작
2) 6~24h 관찰: UI Calibration 카드 ΔECE/연속치, Prometheus 지표 추이 확인
  - inference_calibration_drift_events_total
  - calibration_retrain_recommend_events_total
  - calibration_retrain_cooldown_seconds
3) 원인 진단: 체크리스트 순서로 라벨 정렬/피처 정합성/분포 붕괴 원인 규명
4) 로직 보강(필요 시):
  - 게이팅 강화: “드리프트 + 성능저하(Brier/PR-AUC)” 동시 충족 시 추천
  - 히스테리시스: ΔECE EMA 기반 진입/이탈 임계 분리(예: 진입 0.12 / 이탈 0.08)
  - 경량 재보정: 온도 스케일링만 재적합 후 추세 개선 여부 확인 → 필요시 전체 재학습
5) Drift UI 정렬: 프론트 Drift 기본값을 백엔드 기준과 맞추어 혼선을 줄입니다.
  - 기본 Window=200, |Z|=3.0 + “추천값 재설정” 버튼 제공
  - 운영에서 변경하려면 `frontend/.env`에 아래 힌트 사용
    - VITE_DRIFT_WINDOW_DEFAULT=300
    - VITE_DRIFT_Z_THRESHOLD_DEFAULT=2.5

메모
- 새로 추가된 환경변수: CALIBRATION_ABS_DELTA_MULTIPLIER, CALIBRATION_RECOMMEND_COOLDOWN_SECONDS
- `.env.example`에 한국어 주석과 권장값 예시가 포함되어 있으니 참고하세요.

## 3) Admin Controls

### 실시간(SSE)과 작업 센터

#### Job Center 사용법
1) Admin > Job Center 탭으로 이동합니다.
2) Backfill 카드: live 토글로 최신 스냅샷을 실시간으로 봅니다. 필요한 경우 Refresh로 1페이지 목록을 즉시 조회합니다.
3) Training 카드: auto 폴링으로 최신 작업을 주기적 갱신합니다. Refresh로 즉시 갱신합니다.
4) Labeler: 라벨러 1회 실행(기본 min_age=120s/limit=1000/force=true)과 Promotion status(쿨다운/다음 허용 시각)를 확인합니다.
5) Live Events: 최근 동작/스냅샷 로그를 간단 텍스트로 확인합니다.

권장:

### 위험 작업 확인(Confirm)

## 4) 환경 변수 요약
백엔드 루트 `.env.example`와 프론트엔드 `frontend/.env.example`를 참고하세요.

  - INITIAL_MODEL_MIN_AUC: 최소 ROC AUC (예: 0.65)
  - INITIAL_MODEL_MAX_ECE: 최대 허용 ECE (예: 0.05)
  - (옵션) INITIAL_MODEL_MIN_PRAUC: 클래스 불균형 시 PR-AUC 하한

  - CALIBRATION_BINS: bin 수(보통 10)
  - CALIBRATION_MIN_BIN_SAMPLES: bin 신뢰성 표본(힌트)
  - CALIBRATION_WILSON_Z: Wilson CI z값(95%≈1.96)
  - CALIBRATION_DRIFT_*: 드리프트 기준(절대/상대/연속/윈도우/최소 표본)

  - AUTO_RETRAIN_ENABLED / AUTO_PROMOTE_ENABLED / CALIBRATION_MONITOR_ENABLED
  - INFERENCE_AUTO_LOOP_ENABLED, INFERENCE_AUTO_LOOP_INTERVAL
    - 프론트엔드 비교: VITE_INFERENCE_AUTO_LOOP_INTERVAL
  - (프론트) 실시간 신선도/지연 판단 관련
    - VITE_RISK_FRESH_MS, VITE_SIG_FRESH_MS, VITE_SEVERE_LAG_MULT
    - VITE_USE_MOCKS=1 인 경우 개발용 SSE 모의 데이터로 UI를 검증할 수 있습니다.

  - 수수료: TRADING_FEE_MODE/TAKER/MAKER
  - 기본 크기/자본: LIVE_TRADE_BASE_SIZE, RISK_STARTING_EQUITY
  - 리스크 가드: RISK_DAILY_LOSS_CAP, RISK_MAX_DRAWDOWN, RISK_MAX_SYMBOL_EXPOSURE, RISK_MAX_CONCURRENT_POSITIONS, RISK_ATR_*, RISK_MAX_SLIPPAGE_BPS

  - EXCHANGE_TRADING_ENABLED=false (로컬 기본)
  - BINANCE_TESTNET=true, BINANCE_ACCOUNT_TYPE=spot
  - 실거래 허용은 강력 비권장: 1) EXCHANGE_TRADING_ENABLED=true, 2) BINANCE_TESTNET=false, 3) SAFETY_ALLOW_REAL_TRADING=1

  - Prometheus 스크랩을 위한 METRICS_* 설정과 주요 알림 토글들

## 5) 트러블슈팅
 - 실시간 스트림: SSE 연결이 자주 끊기면 프록시 타임아웃/keep-alive, last-event-id 헤더 처리, 백오프 설정을 점검하세요. 방화벽 환경에서는 폴링으로 폴백하세요.

---

## 감성(Sentiment) 파이프라인 운영 요약

- 목적: 뉴스/소셜 등 감성 점수를 수집→집계(EMA/비율/변동성)→피처 조인(누출 방지)→학습/모델에 활용.

### 핵심 엔드포인트
- POST /api/sentiment/tick: 티크 인제션 {symbol, ts(ms), score, polarity, provider, meta}
- GET /api/sentiment/latest?symbol=XRPUSDT&windows=5m,15m,60m
- GET /api/sentiment/history?symbol=XRPUSDT&start=...&end=...&step=1m&agg=mean
- SSE /stream/sentiment?symbol=XRPUSDT&windows=5m,15m,60m (API Key 필요)

### 환경 변수(백엔드)
- SENTIMENT_EMA_WINDOWS=5m,15m,60m
- SENTIMENT_STEP_DEFAULT=1m
- SENTIMENT_MAX_LATENCY_MS=600000
- SENTIMENT_DEDUP_WINDOW_SEC=120
- SENTIMENT_PROVIDER_TRUST_JSON={}
- SENTIMENT_ENABLE_SSE=true
- SENTIMENT_HISTORY_MAX_POINTS=10000
- SENTIMENT_POS_THRESHOLD=0.0
- SENTIMENT_AGG_WEIGHTS={}
- SENTIMENT_HISTORY_SIZE=100
- SENTIMENT_DISABLE_FAILURE_STREAK=5
- NEWS_SENTIMENT_CACHE_TTL=30

### 학습 제어(감성 포함/제외 및 결측 대체)
- TRAINING_INCLUDE_SENTIMENT=true
- TRAINING_IMPUTE_SENTIMENT=ffill  # ffill|zero|none
- TRAINING_SENTIMENT_FEATURES=sent_score,sent_cnt,sent_pos_ratio,sent_d1,sent_d5,sent_vol_30,sent_ema_5m,sent_ema_15m,sent_ema_60m
- TRAINING_ABLATION_REPORT=true

### 관측지표(Prometheus)
- sentiment_ingest_total / sentiment_ingest_errors_total
- sentiment_ingest_latency_ms
- sentiment_history_query_latency_ms
- sentiment_sse_clients
- feature_sentiment_join_attempts_total / feature_sentiment_join_missing_total

### 장애 대처 체크
- 인제션 실패 증가: provider 응답/스키마 확인, SENTIMENT_DEDUP_WINDOW_SEC 조정 여부 점검
- 지연 급증: SENTIMENT_MAX_LATENCY_MS vs 실제 수집 지연 비교, 네트워크/큐 상태 확인
- 피처 조인 누락: 캔들 close_time 기준 누출 방지 규칙 확인, SENTIMENT_STEP_DEFAULT/EMA 윈도우 재검토
- 학습 성능 하락: ablate_sentiment로 A/B 비교, TRAINING_IMPUTE_SENTIMENT 전환(ffill→zero)

자세한 설명은 docs/sentiment_endpoints.md 및 RUNBOOK.md의 "Sentiment pipeline ops"를 참고하세요.


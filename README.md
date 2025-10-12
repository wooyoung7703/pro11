# OHLCV 실시간 및 검증 가이드

본 프로젝트는 단일 Canonical 테이블(`ohlcv_candles`)을 통해 OHLCV 데이터를 관리하며, REST API 와 WebSocket 실시간 채널을 제공합니다. API/WS 응답이 설정(.env)의 심볼/인터벌과 일치하는지 항상 검증하는 절차를 권장합니다.

## 1. 핵심 환경 변수 (.env)
```
SYMBOL=XRPUSDT
INTERVAL=1m
INGESTION_ENABLED=true  # 실시간 Binance 소비 활성화 시 append 이벤트 발생
```

## 2. 주요 REST 엔드포인트
1) 최근 캔들
```
GET /api/ohlcv/recent?symbol=SYMBOL&interval=INTERVAL&limit=100&include_open=false
```
검증 체크:
- symbol == .env SYMBOL (대문자 비교)
- interval == .env INTERVAL
- candles open_time ASC 정렬
- include_open=false 경우 모든 is_closed == true
- limit 상한(예: 1000) 내부인지

2) 메타
```
GET /api/ohlcv/meta?symbol=SYMBOL&interval=INTERVAL&sample_for_gap=2000
```
- completeness_percent ∈ [0,1] 혹은 null
- largest_gap_bars >= 0

3) 히스토리 (커서 페이징)
```
GET /api/ohlcv/history?symbol=SYMBOL&interval=INTERVAL&limit=500&before_open_time=...&after_open_time=...
```
- open_time ASC
- before/after 동시 지정 금지 (400)
- next_before_open_time / next_after_open_time 커서 활용

## 3. WebSocket 실시간 채널
```
WS /ws/ohlcv
```
초기 수신 메시지 예 (snapshot):
```json
{
	"type":"snapshot",
	"symbol":"XRPUSDT",
	"interval":"1m",
	"candles":[ {"open_time":..., "close_time":..., "open":..., "high":..., "low":..., "close":..., "volume":..., "is_closed":true}, ... ],
	"meta": {"earliest_open_time":..., "latest_open_time":..., "count": N}
}
```
이후 새로 확정된 캔들은 append 이벤트:
```json
{
	"type":"append",
	"symbol":"XRPUSDT",
	"interval":"1m",
	"count": K,
	"candles":[ ... closed only ... ]
}
```
검증 포인트:
- snapshot & append 모두 symbol/interval 일치
- candles ASC
- is_closed == true
- append 첫 open_time > snapshot 마지막 open_time (증분)

## 4. 수동 검증 예시(Python 원라이너)
```bash
curl -s "http://localhost:8000/api/ohlcv/recent?symbol=$SYMBOL&interval=$INTERVAL&limit=20" \
 | python -c "import sys,json;d=json.load(sys.stdin);cs=d.get('candles',[]);ots=[c['open_time'] for c in cs];print('ASC',all(ots[i]<=ots[i+1] for i in range(len(ots)-1)), 'SYMBOL',d.get('symbol'),'INTERVAL',d.get('interval'))"
```

## 5. 자동화 테스트
`tests/test_ohlcv_endpoints.py` : recent/meta/history 정합성
`tests/test_ohlcv_meta_metrics_consistency.py` : meta vs Prometheus 게이지 일치
`tests/test_ws_ohlcv.py` : WebSocket snapshot/append 구조 검증

## 6. 문제 해결 (Troubleshooting)
| 증상 | 원인 후보 | 대응 |
|------|-----------|------|
| snapshot candles 비어있음 | DB 데이터 없음 또는 ingestion 비활성 | 백필/ingestion 활성화 후 재시도 |
| append 이벤트 안 옴 | 실시간 소비자 flush 없음 | 배치 대기 또는 ingestion_enabled 설정 확인 |
| symbol mismatch (4403) | 현재 단일 심볼/인터벌만 허용 | .env 값과 요청 파라미터 동기화 |
| completeness_percent None | 초기 메타 계산/데이터 적음 | 데이터 축적 후 재조회 |

## 7. 향후 확장 아이디어
- repair(갭 복구) 이벤트 WebSocket 배포
- partial(open) 캔들 실시간 옵션화
- 멀티 심볼 매니저 매핑 구조로 확장

---
이 문서는 한글 검증 절차 기준으로 작성되었으며, PR 시 최신 스키마/이벤트 구조 변화가 있다면 함께 갱신해야 합니다.

# XRP Intraday Trading System (Backend Scaffold)

## Overview
Initial scaffolding for the Python backend of the XRP 1–5m intraday automated trading system. This phase provides:
- Configuration model (Pydantic v2)
- FastAPI application with basic health + version endpoints
- Async Postgres pool helper (asyncpg)
- Binance Futures websocket kline consumer skeleton
- Makefile for common developer tasks
- Poetry project definition (`pyproject.toml`)
- Feature computation & snapshot persistence
- Model registry (registration, promotion, metrics, lineage)
- Risk engine skeleton (evaluation + simulated fill)
- Risk persistence (risk_state, risk_positions) with /api/risk/state
- Prometheus metrics endpoint (`/metrics`)
- API Key auth for protected endpoints
- Automated background feature scheduler (periodic snapshot updates)

## 한국어 운영 요약 (Operations Quick Guide)
본 섹션은 실제 운영 중 자주 겪는 상황을 “무엇이 이상인지 → 어떻게 진단 → 어떤 API/메트릭 → 복구/예방” 순으로 빠르게 안내합니다. (영문 전체 설명은 기존 섹션 참조)

### 1. 핵심 컴포넌트 & 루프
| 컴포넌트 | 역할 | 주요 주기(Environment) | 핵심 메트릭/엔드포인트 |
|----------|------|------------------------|------------------------|
| Feature Scheduler | 피처 스냅샷 생성 | FEATURE_SCHED_INTERVAL | feature_scheduler_last_success_timestamp, feature_scheduler_lag_seconds |
| Ingestion (Kline) | 시세(캔들) 수집 | Websocket 실시간 | ingestion_lag_seconds |
| Training Auto Check | 드리프트/캘리브 기반 재학습 여부 판단 | AUTO_RETRAIN_DRIFT_WINDOW (데이터 기반) | auto_retrain_checks_total, auto_retrain_triggered_total |
| Calibration Monitor | Live ECE/Brier 계산 & 드리프트 감시 | CALIBRATION_MONITOR_INTERVAL | inference_live_ece, inference_live_ece_delta |
| Inference Auto Loop | 주기적 확률 산출 & 로그 적재 | INFERENCE_AUTO_LOOP_INTERVAL | inference_log_flush_* / decisions_* (activity summary) |
| News Ingestion | RSS 기사 폴링 & dedup | NEWS_POLL_INTERVAL | news_dedup_ratio, news_source_stall, news_source_anomaly(향후) |

### 2. 빠른 헬스 점검 체크리스트
1) `/health/extended` → DB, ingestion, feature lag OK?
2) 모델 상태: `/api/models/summary` → production 존재 & promote_recommend?
3) 캘리브레이션: `/api/inference/calibration/live` vs `/api/models/production/calibration` → ECE 급격 변동?
4) 재학습 필요? promote_recommend 또는 calibration_retrain_recommendation 게이지 확인.
5) 뉴스 수집: `/api/news/status`(존재 시) 혹은 기사 감소 → 수동 `/api/news/fetch/manual`.

### 3. 공통 이상 시나리오 & 대응
| 증상 | 가능 원인 | 1차 확인 | 조치 |
|------|----------|----------|------|
| Feature lag 상승 | 외부 시세 지연, 스케줄러 예외 | feature_scheduler_last_error_timestamp | 로그 확인 후 재시작 (필요 시 컨테이너 재기동) |
| Inference activity 0 지속 | Auto loop 비활성, feature 없음 | /api/inference/activity/summary | Auto loop enable, feature 정상 생성 여부 확인 |
| Live ECE 급증 | 데이터 분포 변화, 모델 과적합 | /api/inference/calibration/live | 재학습 `/api/training/run` 후 승격 검토 |
| Promotion 실패 | AUC 개선 부족, 허용 열화 초과 | /api/models/promotion/events | 임계값 환경변수 재검토 또는 수동 promote |
| Dedup ratio 급락 | 중복 기사 폭증(한 소스 feed loop), 파싱 실패 | news_dedup_ratio, 소스별 raw/unique | 문제 소스 비활성 / RSS URL 교체 |
| 뉴스 기사 0 지속 | feed 장애 또는 네트워크 | poll 로그, stall threshold 초과 | 수동 fetch, feed 추가, 네트워크 점검 |

### 4. 재학습 & 승격 의사결정 흐름 (요약)
1) Auto 또는 수동 학습 실행 → 모델 registry 기록.
2) `/api/models/summary` 에서 새 모델이 production 대비 AUC 개선 + (Brier/ECE 열화 허용 범위 내) 이면 promote_recommend=true.
3) 자동 승격 엔드포인트(`/api/models/promotion/recommend`) 또는 수동 `/api/models/{id}/promote` 실행.
4) 승격 후 프로덕션 calibration 지표 재확인 (변화 폭 기록).

### 5. 캘리브레이션 운영
- Live vs Production ECE 차이: 절대/상대 임계값 모두 초과 & streak 충족 시 retrain 권고 게이지 1.
- 재학습 후 live ECE 개선되지 않으면: 피처 안정성(분포 shift), label 정의, 샘플 크기 재검토.
- Reliability bins 왜곡(몇몇 bin만 과도) → threshold 전략 재평가 또는 더 많은 데이터 확보.

### 6. 뉴스 수집 운영 가이드 (기본)
- Poll 성공마다 raw_count / unique_count 로 dedup ratio 측정.
- `NEWS_DEDUP_MIN_RATIO` 미만 streak ≥ `NEWS_DEDUP_RATIO_STREAK` → anomaly 후보 (추후 /api/news/sources 에 표출 예정).
- Stall: 마지막 unique 기사 시각이 `NEWS_SOURCE_STALL_THRESHOLD_SEC` 초 초과.
- 백필: `/api/news/backfill?days=N` (feed 제공 한계 안에서만 의미). 과거 부족 기간 재보강.

### 7. 로그 & 모니터링 우선순위
1) 구조적 에러 (Traceback) → 재현 경로/파라미터 기록
2) 메트릭 delta 기반 이상 (ECE jump, dedup 폭락)
3) 운영자 수동 액션 기록 (seed force, manual fetch) → 향후 runbook 개선 피드백

### 8. 장애 대응 기본 절차 (Triage)
1) 분류: (기능/성능/데이터 품질/외부의존)
2) 영향 범위 측정: 모델 의사결정 지연? 지표 왜곡? 기사 결측?
3) 즉시 완화(Mitigation): 재시작, 임시 파라미터 조정, 수동 fetch
4) 근본 원인(RCA): 로그 stack + 최근 배포/환경변수 diff + 외부 API 상태
5) 사후 조치(Post Action): 문서/알람 규칙/임계값 업데이트

### 9. 운영자 온보딩 핵심 5문항 (Self‑Check)
1) Live ECE vs Prod ECE 차이가 의미 있게 커졌을 때 어디를 먼저 보나요? → `/api/inference/calibration/live` + streak/임계값 환경변수
2) 모델 승격 기준은 무엇인가요? → AUC 개선 + Brier/ECE 악화 허용 범위 이하
3) 뉴스 수집 stall 은 어떻게 감지? → 마지막 unique 기사 timestamp vs threshold
4) Dedup anomaly 의미는? → 반복/저품질 feed 또는 파서 오류 가능성 경고
5) Feature lag 발생 시 1차 조치는? → Scheduler 오류 로그 & ingestion 정상 여부 확인

> 이 섹션은 신규 엔드포인트(/api/news/sources 등) 확장 시 추가 갱신 예정.

### (신규) 뉴스 고도화 엔드포인트 & UI 사용 가이드
뉴스 수집/분석 UI 전면 개편에 따라 다음 엔드포인트 및 프론트 기능이 추가되었습니다.

#### 1. 개요
| 기능 | 엔드포인트 | 설명 |
|------|------------|------|
| 소스 상태/헬스 | `GET /api/news/sources` | 각 RSS 소스별 stall/anomaly/backoff, dedup 비율, 헬스 점수, (선택) dedup 히스토리 |
| 집계 요약 | `GET /api/news/summary` | 최근 window 내 총 기사수, 소스 분포, sentiment 통계(avg/std/pos/neg/neutral) |
| 전문/요약 검색 | `GET /api/news/search` | 제목/요약/본문(저장된 경우) ILIKE 검색 (days, limit) |
| 수동 즉시 fetch | `POST /api/news/fetch/manual` | 폴링 주기 기다리지 않고 즉시 한 번 fetch 실행 |
| 과거 백필 | `POST /api/news/backfill?days=N` | 최근 N일 내 결측 구간 재수집 시도 (feed 제공 범위 한계) |
| 기사 Delta 조회 | `GET /api/news/recent` | since_ts 기반 증분(delta) 또는 초기 전체 목록 (summary_only 축약) |
| 단일 기사 본문 | `GET /api/news/article/{id}` | 기사 body (지연 로딩) |

#### 2. `/api/news/sources` 응답 필드 (요약)
```
{
	"sources": [
		{
			"source": "coindesk",
			"score": 0.83,          // (0~1) 내부 계산 헬스 점수 (stall/anomaly 패널티 반영)
			"stalled": false,       // 최근 unique 기사 없음 (threshold 초과)
			"dedup_anomaly": false, // dedup 비율 저하 or 변동성 과다 탐지
			"backoff_remaining": 0, // 재시도 backoff 잔여 (초)
			"last_unique_ts": 1696234000,
			"dedup_last_ratio": 0.42,
			"dedup_history": [0.35,0.40,0.42,...] // include_history=1 인 경우 최근 n개 비율
		}
	],
	"dedup_history": { "coindesk": [{"ts":1696234000,"raw":12,"kept":5,"ratio":0.41}, ...] }
}
```
사용 팁:
- stall + anomaly 동시발생 소스는 우선 RSS URL/포맷 변경 여부 점검
- dedup_last_ratio 급락 후 회복 없으면 disable → 수동 점검 후 재활성

#### 3. `/api/news/summary`
파라미터: `window_minutes` (기본 1440). 예: 최근 2시간 `?window_minutes=120`.
응답 예시:
```
{
	"window_minutes": 120,
	"total": 340,
	"sources": [ {"source":"coindesk","count":90,"ratio":0.265}, ... ],
	"sentiment": { "avg":0.12, "std":0.34, "pos":120, "neg":45, "neutral":175 }
}
```
운영 활용:
- source ratio 급격 변화 → 특정 feed flood 여부 (중복 또는 루프) 조사
- sentiment avg 양/음 극단 이동 → 모델 입력 특성(뉴스 sentiment feature) 분포 안정성 확인 필요

#### 4. `/api/news/search`
파라미터: `q`(필수, 2+ chars), `days`(기본 7), `limit`(기본/최대 정책), `offset`.
용례:
```
GET /api/news/search?q=SEC&days=30&limit=200
```
검색 중 UI 자동 폴링은 기사 리스트 덮어쓰기 방지 위해 중단(스토어 처리). 검색 종료 후(clear) 자동 갱신 재개.

#### 5. 수동 운용 시나리오
| 상황 | 액션 | 후속 |
|------|------|------|
| 특정 소스 stall | `/api/news/sources` 로 stalled 확인 → RSS 원문 접속 테스트 | URL 교체 또는 backoff 해제 후 모니터링 |
| dedup anomaly 반복 | dedup_history 패턴 (raw 폭증 vs kept 감소) 비교 | feed 변조/중복 루프 여부 조사 |
| 과거 특정 일자 기사 부족 | `/api/news/backfill?days=7` | summary 재확인 → 개선 없으면 feed 한계 |
| 즉시 최신 기사 필요 | `/api/news/fetch/manual` | sources 패널 score/ratio 영향 관찰 |

#### 6. 프론트 UI (NewsView.vue) 주요 변경
- Source Health 패널: score/stall/anom + dedup sparkline
- Summary 패널: window 슬라이더 minutes 변경 → 즉시 요약 새로고침
- 검색: 300ms debounce, 2글자 이상 시 서버 검색; 결과 모드에서 자동 delta fetch 일시 중단
- Manual Controls: Manual Fetch / Backfill / Summary / Sources 버튼

#### 7. 추적할 Prometheus 지표 (뉴스)
| Metric | 의미 | 대응 |
|--------|------|------|
| news_dedup_ratio | 전체 dedup unique/raw 비율 | 급락 시 /api/news/sources 로 원인 소스 탐지 |
| news_source_stall{source} | stall=1 | feed 접근성/콘텐츠 변경 점검 |
| news_source_anomaly{source,type} | anomaly=1 (low_ratio/volatility) | 중복 폭발 또는 노이즈 feed 필터 |

#### 8. 추후 예정 (Roadmap)
#### 9. 기사 안 나올 때(무기사) 진단 체크리스트
1) RSS 피드 환경변수 설정 여부
	- `NEWS_RSS_FEEDS` 비어 있으면 수집기 Fetcher 0개로 시작 → 아무 기사도 생성 안 됨
	- 예: `NEWS_RSS_FEEDS=https://feeds.feedburner.com/CoinDesk,https://www.coindesk.com/arc/outboundfeeds/rss/` (쉼표 구분)
2) 디버그 엔드포인트 확인
	- `GET /api/news/debug` → `sources` 배열 길이, `fetch_runs` 증가 여부, `articles_ingested` 값 증가 확인
	- `backoff` 값이 큰 양수면 직전 오류로 백오프 대기 중
3) 권한/API Key
	- UI/API 호출에 `X-API-Key` 누락되면 보호 엔드포인트(status/recent 등) 401/403 → 프론트 기사 목록 비어 보일 수 있음
4) Delta fetch since_ts 영향
	- 클라이언트가 `since_ts` 로 delta 요청 시 서버가 `delta=true` 이고 신규 기사 없으면 빈 배열 (정상) → `lastLatestTs` 초기화 or 수동 새로고침으로 전체 목록 재호출
5) DB purge 영향
	- 시작 시 `demo_feed` 자동 purge 수행 → 과거 합성 기사만 존재했다면 초기에는 0개로 보일 수 있음 (실제 RSS 들어오면 채워짐)
6) feed 자체 응답 비정상
	- RSS URL 직접 curl / 브라우저 열어 200 + entries 존재 확인
7) 중복 필터로 모두 드롭
	- 같은 hash 기사 반복 → dedup 으로 unique 0 → `/api/news/sources` 에서 dedup_last_ratio 0 인지 확인

간단 점검 순서: `GET /api/news/debug` → sources>0 ? → fetch_runs 증가? → articles_ingested 증가? 없으면 feed 응답 또는 backoff 확인.

추가 상세 (오류 원인 추적):
`GET /api/news/debug?verbose=1` → `verbose.last_error` / `last_error_ts` / `error_streak` / `last_fetch_latency` 로 개별 feed 상태 심층 확인.

- 검색 결과 내 sentiment facet / 소스별 필터
- 소스 자동 disable/reenable 정책 지표화
- dedup anomaly 상세 타입(variability vs sustained drop) 분리

---

---

## Frontend Console (Vue 3 + Vite + Pinia + Tailwind)
운영·모델·리스크 모니터링을 위한 경량 웹 UI 가 추가되었습니다.

### 주요 기능
- 대시보드: Ingestion/Feature 스케줄러 상태, 지연/스파크라인
- Trading Activity 패널 (신규): 최근 모델 의사결정 활동 심박(heartbeat) 지표
- Drift: 주요 피처 z-score 및 drift 플래그 스캔
- Metrics: 프로덕션 모델 AUC/Accuracy/Brier/ECE
- Training: 최근 학습 잡 테이블 (정렬/상태)
- Inference Playground: 최신 피처 기반 확률/threshold override & 히스토리
- Calibration: Production vs Live (Brier/ECE/MCE, reliability bins, drift streak)
- Risk: Equity/Drawdown/Utilization/Positions 스파크라인 포함
- Admin (탭 통합 콘솔): Actions / Risk / Inference / Calibration / Training / Metrics / Drift 한 화면 탭 전환
- Toast 기반 글로벌 에러 알림 (Axios 인터셉터)

### 개발 실행 (Hot Reload)
```
docker compose -f docker-compose.dev.yml up -d --build
# 프론트엔드: http://localhost:5173  (Vite)
# 백엔드:    http://localhost:8000
```
API Key 입력창에 키를 넣으면 localStorage(`api_key`)에 즉시 저장되며 모든 보호 API 호출 시 `X-API-Key` 헤더로 전송됩니다. 값이 없으면 기본 dev 키(`dev-key`)를 초기값으로 사용.

### 프로덕션 빌드 (Nginx 서빙 + /api 프록시)
`docker-compose.yml` 에 `frontend` 서비스가 추가되었습니다.
```
docker compose build frontend
docker compose up -d frontend
# 접속: http://localhost:8080 (기본 FRONTEND_PORT=8080)
```
프록시 규칙 (nginx):
- `/api/` → `app:8000/api/`
- `/metrics`, `/admin/`, `/health` 등 백엔드 직접 전달
- 정적 자원(js/css/svg/png 등) `immutable` + 1년 캐시, `index.html` 은 `no-cache`

프론트 컨테이너는 `BACKEND_ORIGIN` 환경변수 (기본 `http://app:8000`) 로 주입된 `window.__BACKEND_BASE` 값을 사용하므로 외부 백엔드로 라우팅하려면:
```
BACKEND_ORIGIN=https://your-api.example.com docker compose up -d --build frontend
```

### 구조 개요
```
frontend/
	src/
		lib/http.ts          # Axios 인스턴스 (API Key 헤더 + 에러 토스트)
		stores/              # Pinia 스토어 (ingestion, trainingJobs, inference, calibration, risk, toast)
		                     # + tradingActivity (신규: /api/inference/activity/summary 폴링)
		views/               # 각 페이지 컴포넌트
		components/ToastContainer.vue
		App.vue / router/
```

### 글로벌 에러 처리
`http.ts` 응답 인터셉터에서 실패시 friendly message 추출 후 `toast` 스토어에 `error` 토스트 push. 401 health/polling 과도 스팸은 simple heuristic 으로 skip.

### Request Correlation
백엔드 미들웨어가 모든 응답에 `X-Request-ID` 헤더를 부여. (프론트 토스트 detail에 노출하고 싶다면 후속 작업에서 interceptor 확장 가능)

### Admin 통합 탭
`/admin` → 단일 탭 콘솔 (`AdminConsole.vue`) 로 진입. 마지막 활성 탭은 localStorage 로 기억.

### Trading Activity 패널 (신규)
대시보드 상단에 최근 inference 의사결정 빈도를 요약하는 패널이 추가되었습니다.

데이터 소스:
- 백엔드 엔드포인트: `GET /api/inference/activity/summary`
	- `last_decision_ts` (없으면 '-')
	- `decisions_1m`, `decisions_5m` (최근 1분/5분 의사결정 카운트)
	- `auto_loop_enabled`, `interval` (자동 의사결정 루프 on/off 및 주기초)

색상 규칙:
- 5분 카운트(decisions_5m) == 0 → idle (회색)
- 0 < decisions_5m < 5 → warming (호박색)
- ≥ 5 → active (녹색)

Last Decision 레이블:
- <60s: '<1m'
- <60m: 'Xm'
- ≥60m: 'X.Yh'

Idle Alert (토스트):
- 5분 동안 decisions_5m == 0 이고 idleStreakMinutes ≥ 5 일 때 경고 토스트 1회 발생
- 추가 5분 단위(bucket) 경과 시마다 1회 재알림 (중복 스팸 억제)
- 토스트 스토어 API: `toast.warn(message, detail)` 사용

구현 참고:
- `frontend/src/stores/tradingActivity.ts` : 10초 주기로 summary 폴링 + idleStreakMinutes 누적
- `Dashboard.vue` : 패널 렌더 및 색상/상태 계산, idle alert 타이머(60초) 별도 운용

운영 인사이트:
- Auto loop 활성인데 idle 지속 → feature snapshot / ingestion 지연 의심
- errors_total 증가 & decisions_* 낮음 → 모델 artifact 손상 / 예외 로그 확인 필요
- warming 상태 지속 (예: 10분 이상) → 전략 주기(interval) 대비 기대 빈도 재검증

향후 확장 아이디어:
- WebSocket/SSE 로 실시간 push (폴링 간격 축소)
- 연속 idle 구간 길이 시각화 (sparkline)
- 최근 의사결정 확률 히스토그램 미니 패널 연동

### 향후 개선 제안(문서화 scope)
- 간단 Smoke Test (헬스/버전 + 주요 스토어 fetch) Vitest
- 다크/라이트 테마 전환 상태 기억 (현재 메모리만)
- Request ID 토스트 detail 반영

### 스크린샷 Placeholder (추후 첨부)
| View | 설명 |

---

## 연속성(Continuity) & Gap Orchestration (신규)

### 목표
1년(365일) 구간에 대해 OHLCV 캔들이 인터벌 간격으로 단 한 개의 누락(gap) 없이 존재하도록 유지하고, 실시간 스트림 중 발생 가능한 간헐적 누락을 신속히 탐지·복구(MTTR < 5분)하는 것을 목표로 합니다.

### 구성 요소
| 컴포넌트 | 파일 | 역할 |
|----------|------|------|
| Gap Consumer (실시간) | `kline_consumer.py` | out-of-order / 누락 open_time 감지, gap_segments insert/merge 요청 |
| Gap Repository | `gap_repository.py` | gap 세그먼트 CRUD + merge_or_insert_gap (단순 병합) |
| Backfill Service (기존) | `gap_backfill_service.py` | 단일 세그먼트 FIFO 복구 (_recover_gap) |
| Gap Orchestrator (신규) | `gap_orchestrator_service.py` | 우선순위 큐 기반 동시성 복구 (남은 bars DESC) |
| Backfill Run Audit | `backfill_run_repository.py` | 전체 범위(배치) 백필 실행 추적 (status/에러/로드 bar) |
| Delta Endpoint 개선 | `/api/ohlcv/delta` | overlap(=1 interval) 포함하여 off-by-one 미스 방지 |

### DB 스키마 확장
Migration `20251003_0012_backfill_and_gap_ext` 에서 추가/변경:
* `ohlcv_backfill_runs` (범위형 백필 감사)
* `gap_segments` 컬럼: `retry_count`, `last_attempt_at`, `merged`

백필 Runs 필드:
| 필드 | 설명 |
|------|------|
| from_open_time / to_open_time | (ms) 대상 구간 경계 |
| expected_bars | 계산된 기대 캔들 수 ((to - from)/interval_ms + 1) |
| loaded_bars | 실제 적재된 bars (진행) |
| status | pending|running|success|partial|error |
| attempts | 시작 시도 횟수 |
| last_error | 최종 오류 메시지 (부분 실패 기록) |

### Gap 병합 정책 (v1)
`merge_or_insert_gap`:
1. 새 세그먼트와 겹치는(open|partial) 기존 세그먼트 SELECT ... FOR UPDATE
2. 없으면 그대로 insert
3. 있으면 모든 기존 세그먼트를 recovered 표시(merged) 후 합쳐진 최소 from / 최대 to 범위 하나 생성
4. missing_bars = Σ(existing remaining_bars) + new_missing (정확 재계산 간소화) → TODO: 실제 캔들 카운트 기반 재산출

### Orchestrator 작동 개요
1. 주기(poll_interval)마다 `GapRepository.load_open` → priority queue 재구성
2. 우선순위: remaining_bars DESC, detected_at ASC
3. concurrency N 만큼 worker 태스크 생성 → 각 worker는 큐에서 gap pop → 기존 BackfillService `_recover_gap` 재사용
4. 복구 후 gap 상태 변경(BackfillService side-effects) → 메트릭 자동 갱신

### Delta Overlap
`/api/ohlcv/delta?since=...`는 내부적으로 `since - interval_ms` 경계를 포함한 1개 이전 캔들을 추가로 반환 → 클라이언트는 open_time 중복을 set 기반 제거. Repair / late fill 직후 누락 방지.

### 운영 Runbook (요약)
| 증상 | 진단 순서 | 해결 |
|------|-----------|------|
| gap_segments open 증가 | Prometheus: `kline_gap_orchestrator_queue_size` / `kline_gap_backfill_queue_size` 확인 | Orchestrator 동시성 상향(concurrency), REST rate 제한 모니터 |
| 특정 gap 세그먼트 장시간 open | gap row `detected_at` vs 현재 시간 | 수동 REST 히스토리 fetch 후 bulk_upsert → repository.mark_recovered |
| completeness 365d 급락 | (TODO) `ohlcv_completeness_365d_ratio` 게이지 | 백필 run 생성 / 장기 구간 외부 재수집 |
| merge 잦음 | `kline_gap_merge_events_total` 증가 | 실시간 소비 지연 → ingestion lag 조사 (네트워크/소스 스트림) |
| delta 재접속 후 초기 캔들 누락 | 클라이언트 로그 open_time gap | overlap 활성화 여부 점검 / since 계산 버그 수정 |

### 향후 계획
- 정확 missing 재산출 (merge시 DB candle count 기반)
- 365d continuity batch 스캐너 스케줄러 + 게이지 업데이트
- Late fill 처리 및 부분 indicator 재계산 최소화 범위 적용
- MTTR 측정 지점 삽입 및 알람 규칙 추가

### 수동 백필 예시 흐름
1. 대상 범위 계산 (예: 30일 전 ~ 오늘) → interval_ms 기반 expected_bars
2. `BackfillRunRepository.create` → run_id
3. 실행 워커가 N개씩 REST fetch -> bulk_upsert -> update_progress
4. 누락 남음 & 재시도 제한 도달 → status partial + last_error 기록
5. 완전 성공 → success & completeness 게이지 리프레시

---
\n+---
## (신규) OHLCV Streaming 확장 메트릭 & 알림 요약

최근 추가된 실시간 캔들 스트리밍 개선 사항과 관련 Prometheus 메트릭/알람 요약입니다.

### 1. WebSocket 이벤트 확장
| 이벤트 | 설명 | 주요 필드 |
|--------|------|-----------|
| `partial_update` | 진행 중 (미확정) 캔들의 중간 값 갱신 | candle(open_time, high/low/close, volume…) |
| `partial_close` | partial 이 확정되어 최종 close → append 전환 | candle + `latency_ms`(optional) |
| `append` | 새로 확정된 1개 또는 다수 캔들 | candle(s) is_closed=true |
| `repair` | 과거 누락/오류 캔들 교정 | candles[] (open_time 매칭 upsert) |
| `gap_detected` | forward append 중 연속성 깨짐 탐지 | detail(from_ts, to_ts, missing) |
| `gap_repaired` | gap 영역 모두 복구 완료 | detail(gap_id, size, repaired_ts) |

### 2. Delta API (/api/ohlcv/delta)
입력: `since=<last_closed_open_time>`
응답: `{ candles: [...], repairs: [{ open_time, candle, repaired_at }], truncated: bool }`
- truncated=true 인 경우: limit 도달 → 클라이언트는 snap 또는 반복 delta 로 재시도

### 3. 프론트 동기화 전략
1) WebSocket snapshot 수신 → baseline 상태
2) 재연결 시 delta 호출 전 도착한 append/repair 는 인터셉터로 버퍼링
3) delta 결과 적용 후 버퍼 flush → 단조/정합 보장

### 4. Gap Status Endpoint
`GET /api/ohlcv/gaps/status?symbol=...&interval=...` → `segments[]` (from_ts, to_ts, missing, state=open/closed)
프론트: `GapOverlayMiniChart` 컴포넌트가 gapSegments 강조 렌더링 (open=Tomato, closed=Gold)

### 5. Prometheus 메트릭 표
| 메트릭 | 유형 | 설명 | 주요 라벨 |
|--------|------|------|-----------|
| `ohlcv_ws_partial_update_sent_total` | Counter | partial_update 이벤트 송신 누계 | symbol, interval |
| `ohlcv_ws_partial_close_sent_total` | Counter | partial_close 이벤트 송신 누계 | symbol, interval |
| `ohlcv_partial_close_latency_ms` | Histogram | partial 시작→close 지연(ms) | symbol, interval |
| `ohlcv_partial_close_last_latency_ms` | Gauge | 마지막 partial close 지연(ms) | symbol, interval |
| `ohlcv_ws_gap_detected_sent_total` | Counter | gap_detected 이벤트 수 | symbol, interval |
| `ohlcv_ws_gap_repaired_sent_total` | Counter | gap_repaired 이벤트 수 | symbol, interval |
| `ohlcv_delta_requests_total` | Counter | delta 호출 수 | symbol, interval, truncated (true/false) |
| `ohlcv_gap_open_segments` | Gauge (예정) | 현재 open gap 개수 | symbol, interval |

### 6. PromQL 활용 예시
```promql
# partial close p95 (5분 윈도우)
histogram_quantile(0.95, sum by (le)(rate(ohlcv_partial_close_latency_ms_bucket[5m])))

# delta truncated 비율 (5분)
sum(rate(ohlcv_delta_requests_total{truncated="true"}[5m]))
/ ignoring(truncated)
	sum(rate(ohlcv_delta_requests_total[5m]))

# 최근 10분 내 gap 감지 대비 복구 비율
sum(increase(ohlcv_ws_gap_repaired_sent_total[10m]))
/ on (symbol, interval)
	sum(increase(ohlcv_ws_gap_detected_sent_total[10m]))
```

### 7. Alert 규칙 (요약)
| 알람 | 조건 (예시) | 의미 |
|------|-------------|------|
| OhlcvPartialCloseLatencyHighP95 | p95 > 1500ms 5m 지속 | partial 처리 지연 증가 |
| OhlcvDeltaTruncatedRatioHigh | truncated 비율 > 0.2 10m | 클라이언트 누락 대량, limit 협소 가능 |
| OhlcvOpenGapStuck | open gap 개수 5분 전보다 감소 없음 & >0 | gap 복구 지연 |
| OhlcvPartialCloseLatencySustainedDegradation | p95 30m 이동평균 > 임계 | 장기 성능 저하 |

자세한 조건은 `infra/prometheus/ohlcv_alert_rules.yaml` 참고.

### 8. 인디케이터 모듈 (P1)
`frontend/src/lib/indicators/` 에 SMA/EMA 증분 계산 유틸 추가.
| 항목 | 설명 |
|------|------|
| `sma.ts` | ring buffer 기반 O(1) append, window 포함 repair sum 보정 |
| `ema.ts` | alpha 기반 O(1) append, repair 시 해당 인덱스 이후 재계산 |

### 9. 향후 로드맵 (요약)
- WS → Delta 자동 재동기화 성능 계측 (벤치 스크립트 존재)
- 추가 지표: VWAP, RSI, ATR, Gap Fill Rate Gauge
- gap severity classification & SLA 기반 알람
- partial preview 기반 실시간 예측(지표 투영) 실험

---
| Dashboard | Ingestion lag + sparkline |
| Drift | 피처 Z-score 리스트 |
| Calibration | Live vs Prod metrics + bins |
| Risk | Equity/Drawdown/Exposure 패널 |
| Admin | 탭 통합 콘솔 |


## Quick Start
1. Install dependencies:
```
poetry install --no-root
```
2. Run API (reload):
```
poetry run uvicorn backend.apps.api.main:app --reload --port 8000
```
3. Visit:
- Health: http://localhost:8000/healthz
- Version: http://localhost:8000/api/version

### Docker (Runtime & Tests)

빌드 (런타임만, dev 의존 제외):
```
docker build -t xrp-app:latest .
```
테스트용(dev 의존 포함) 이미지:
```
docker build --build-arg DEV_DEPS=true -t xrp-app-dev:latest .
```
단축 Make 타겟:
```
make docker-build        # 런타임
make docker-build-dev    # dev(테스트)
make docker-test         # dev 이미지에서 pytest 실행
make docker-run          # 0.0.0.0:8000 서비스
```
컨테이너 직접 테스트 실행:
```
docker run --rm xrp-app-dev:latest poetry run pytest -q
```
API 실행:
```
docker run --rm -p 8000:8000 xrp-app:latest
```
메트릭 확인:
```
curl -s http://localhost:8000/metrics | head
```

### (New) One-shot Schema Initialization
신규 환경(빈 Postgres)에 모든 애플리케이션 테이블을 한 번에 생성하려면 API Key 포함하여 아래 엔드포인트 호출:

```
POST /api/admin/schema/ensure  (Header: X-API-Key: <your_key>)
```
응답 예:
```
{
	"status": "ok",
	"tables": [
		"news_articles",
		"kline_raw",
		"feature_snapshot",
		"model_inference_log",
		"training_jobs",
		"model_registry",
		"model_metrics_history",
		"model_lineage",
		"retrain_events",
		"promotion_events",
		"risk_state",
		"risk_positions",
		"gap_segments"
	]
}
```
해당 호출은 idemponent (여러 번 호출해도 안전) 하며, 기존 단일 뉴스 전용 보장 `/api/news/schema/ensure` 를 포함해 전체 스키마를 커버합니다.

Admin UI: 프론트엔드 Admin > Actions 탭에 "전체 테이블 생성" 버튼이 추가되어 동일한 엔드포인트를 호출합니다 (idempotent). 초기 세팅 시 curl 대신 버튼을 눌러 생성 가능.

로컬에서 스크립트 기반 전체 DB 재생성과 동시 초기화를 하고 싶다면 추후 `scripts/reset_database.py` 확장을 사용할 예정입니다. (현 시점: `--with-news` 는 뉴스 테이블 생성만 수행)

초기 스타트업 시 자동 생성을 원하면 컨테이너 시작 후 헬스체크 전에 CI/CD 파이프라인에서 위 엔드포인트를 한 번 호출하거나, 별도 bootstrap 잡(예: Make / invoke 스크립트)에서 호출하도록 구성하십시오.

#### Docker 첫 설치 (빈 DB) 빠른 절차
1. `.env` 혹은 compose 환경에 `AUTO_SCHEMA_ENSURE=true`
2. `docker compose up -d` 후 API 로그에서 `auto_schema_ensure completed tables=` 확인
3. Admin > Actions 의 "전체 테이블 생성" 버튼 한번 더 (선택, idempotent 검증)
4. baseline 모델이 필요하고 seed 자동 삽입 실패 로그가 있다면 `/api/models/register` 로 수동 추가

## Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| APP_ENV | dev | Environment (dev/staging/prod) |
| POSTGRES_HOST | localhost | DB host |
| POSTGRES_PORT | 5432 | DB port |
| POSTGRES_DB | xrp_trading | Database name |
| POSTGRES_USER | xrp_user | Username |
| POSTGRES_PASSWORD | xrp_pass | Password |
| SYMBOL | XRPUSDT | Trading symbol |
| INTERVAL | 1m | Kline interval |
| API_KEY | dev-key | API key for protected endpoints |
| FEATURE_SCHED_INTERVAL | 60 | (Optional) Seconds between automatic feature computations (currently hardcoded; wire env later) |
| INGESTION_ENABLED | false | Set to 'true' to start Binance kline websocket consumer |
| HEALTH_MAX_FEATURE_LAG_SEC | 180 | Feature scheduler last success max acceptable lag |
| AUTO_RETRAIN_LOCK_KEY | 821337 | Postgres advisory lock key for distributed retrain exclusion |
| HEALTH_MAX_INGESTION_LAG_SEC | 120 | Ingestion last message max acceptable lag |
| DRIFT_Z_THRESHOLD | 3.0 | Absolute z-score threshold signaling feature drift |
| MODEL_ARTIFACT_DIR | artifacts/models | Directory for saving trained model artifacts |
| AUTO_RETRAIN_ENABLED | false | Enable background drift-triggered training |
| AUTO_RETRAIN_DRIFT_FEATURES | (unset) | Comma list of features to evaluate for drift (e.g. ret_1,ret_5,ret_10,rsi_14,rolling_vol_20,ma_20,ma_50) |
| AUTO_RETRAIN_DRIFT_MODE | max_abs | Drift aggregation: max_abs | mean_top3 |
| AUTO_RETRAIN_DRIFT_WINDOW | 200 | Window size used for drift z-score computation |
| AUTO_PROMOTE_MIN_AUC_IMPROVE | 0.01 | Minimum relative AUC improvement for promotion (1% default) |
| INFERENCE_PROB_THRESHOLD | 0.5 | Default probability threshold for positive (long) decision in inference |
| INFERENCE_AUTO_LOOP_ENABLED | false | true 시 백엔드가 내부 주기로 자동 inference 실행(로그 적재) |
| INFERENCE_AUTO_LOOP_INTERVAL | 15.0 | 자동 inference 간격(초) |
| INFERENCE_LOG_QUEUE_MAX | 5000 | Max in-memory queued inference log entries before drops |
| INFERENCE_LOG_FLUSH_BATCH | 200 | Max records per DB flush batch |
| INFERENCE_LOG_FLUSH_INTERVAL | 2.0 | Flush interval (seconds) for async inference log worker |
| INFERENCE_LOG_FLUSH_MIN_INTERVAL | 0.25 | Minimum interval when adapting flush speed under load |
| INFERENCE_LOG_FLUSH_HIGH_WATERMARK | 0.7 | Queue occupancy ratio to accelerate flushing |
| INFERENCE_LOG_FLUSH_LOW_WATERMARK | 0.2 | Queue occupancy ratio to decelerate (revert toward base interval) |
| RISK_METRICS_INTERVAL | 5.0 | Interval (seconds) between sampling in-memory risk engine gauges |
| NEWS_SOURCE_STALL_THRESHOLD_SEC | 900 | Seconds of inactivity (no unique ingest) after which a news source is marked stalled |
| FAST_STARTUP_EAGER_COMPONENTS | (unset) | Comma list of components to still start even when FAST_STARTUP=true (e.g. news_ingestion,feature_scheduler) |
| NEWS_POLL_INTERVAL | 90 | News RSS poll interval (seconds) when loop running |
| NEWS_DEDUP_MIN_RATIO | 0.15 | Dedup unique/raw 비율이 이 값 미만일 때 low ratio streak 증가 (anomaly 후보) |
| NEWS_DEDUP_RATIO_STREAK | 3 | Low ratio 연속 N회 시 dedup anomaly 진입 조건 |
| NEWS_DEDUP_VOLATILITY_WINDOW | 10 | Dedup ratio 변동성(표준편차) 계산에 사용하는 최근 poll 개수 |
| NEWS_DEDUP_VOLATILITY_THRESHOLD | 0.25 | 표준편차가 이 값 이상이면 volatility anomaly 신호 |
| AUTO_SCHEMA_ENSURE | false | true 시 서버 startup 에 전체 테이블 ensure_all 자동 실행 (초기 Docker 첫 설치 편의) |
- auto_retrain_lock_acquire_total – attempts to acquire the lock
- auto_retrain_lock_acquired_total – successful acquisitions
- auto_retrain_lock_held – gauge (1 when this instance holds the lock)
make lint      # Ruff check

### News Bootstrap / Historical Backfill (신규)

최초 배포 또는 feed 지연으로 뉴스 DB가 비어 있는 경우 자동/수동 부트스트랩으로 과거 N일 히스토리를 빠르게 확보할 수 있다.

1. 자동 스타트업 부트스트랩 (환경변수)
```
NEWS_STARTUP_BOOTSTRAP_DAYS=7
NEWS_STARTUP_BOOTSTRAP_ROUNDS=4
NEWS_STARTUP_BOOTSTRAP_INTERVAL=5   # 라운드 사이 sleep_seconds
NEWS_STARTUP_BOOTSTRAP_MIN_ARTICLES=40  # (선택) 기사 누적 목표
```
조건: 서비스 시작 시 `news_articles` 테이블에 기존 레코드가 없을 때 1회 실행. 이미 데이터가 있으면 skip.

2. 수동 부트스트랩 엔드포인트
```
POST /api/news/bootstrap
Body:
{
	"rounds": 4,
	"sleep_seconds": 3,
	"recent_days": 7,
	"min_total": 50,
	"per_round_backfill_days": 7
}
```
라운드별 순서: (a) poll_once 실시간 수집 → (b) backfill (RSS 항목 중 recent_days 기간 내, 중복 hash 제거). 2회 연속 신규 증가량 0(plateau) 또는 min_total 도달 시 조기 중단.

3. 기존 `/api/news/backfill` 과 차이
| 항목 | /api/news/backfill | /api/news/bootstrap |
|------|--------------------|---------------------|
| 라운드 반복 | 단일 패스 | 다회(rounds) |
| 실시간 poll 포함 | 없음 | 포함 (각 라운드 첫 단계) |
| 조기 종료 조건 | 없음 | plateau 또는 min_total |
| 라운드별 통계 | 없음 | stats 배열 (inserted_live, inserted_backfill 등) |

4. 상태 확인
```
GET /api/news/debug?verbose=1
```
verbose 필드에 `bootstrap_running`, `bootstrap_started_ts`, `bootstrap_finished_ts` 포함.

5. 흔한 사용 흐름
1) 환경변수 세팅 후 첫 실행 → 빈 DB 자동 부트스트랩 → 기본 히스토리 확보
2) 추가 히스토리가 더 필요하면 `/api/news/bootstrap` 로 rounds 확장 실행
3) 정상 주기 poll loop 가 최신 기사만 누적

문제 상황 대응: "최신 기사가 전혀 안 들어온다" → 우선 부트스트랩으로 최소 기반 확보 후 feed 헬스(stall/anomaly) 진단.
make type      # mypy strict type check
make test      # Run pytest

## 다국어(UI 한글화) 안내
본 프로젝트는 현재 한국어 UI를 정적 치환 방식으로 제공합니다.

### 전략
- 비즈니스 로직/응답 필드 키(probability, threshold 등)는 영문 유지 (호환성 & 로그 가독성)
- 화면 라벨/툴팁만 한국어로 치환
- 환경설정(.env) 값과 API 응답(interval 등)을 대시보드에서 비교 표기하여 설정 동기화 상태 가시화

### 확장 가이드 (추가 언어)
1. `src` 최상단에 `i18n/strings.ts` 생성 → key/value 구조 정의
2. 컴포넌트 내 하드코딩 문자열을 키 참조로 교체
3. 사용자 언어 선택(로컬 스토리지 or 쿼리파라미터) + 계산된 computed로 노출
4. 새 언어 추가 시 README 테이블에 키/번역 열 추가

### 검증(Validation) 메시지
`validateResponse`는 한글 경고/오류 포맷을 사용: "필수 필드 없음", "필드 타입 불일치" 등. 추가 규칙은 `validation/validate.ts` 내 로직을 확장.

### 추가 개선 아이디어
- Lazy load 번역(JSON chunk) 도입
- 서버측 Accept-Language 헤더 기반 기본 언어 자동 선택
- 다국어 토스트/알람 표준 severity 아이콘 도입

---

## 연속성(Continuity) Phase2 추가 문서 (Late Fill · MTTR · Scanner)  
이 섹션은 기존 "연속성 & Gap Orchestration" 설명을 확장하여 Phase2에서 도입된 Late Fill 처리, MTTR 측정, 365d 스캐너 사용 예시를 정리합니다.

### 1. Late Fill 처리 (Out-of-Order Closed Candle)
실시간 WebSocket 수신 중 드물게 과거(이미 last_closed 포인터 이전)의 확정 캔들이 지연 도착할 수 있습니다.

핸들링 로직(`kline_consumer.py`):
1. 수신 kline `open_time < _last_closed_open_time` 이고 확정(`x=True`)인 경우 Late Fill 분기 진입
2. 즉시 canonical upsert (중복 방지: ON CONFLICT DO UPDATE) 수행
3. `kline_late_fill_total` Counter 증가
4. (Phase2.5 예정) 해당 open_time 이 열린 gap 세그먼트 범위 안이면 `remaining_bars` 감소 또는 세그먼트 split → 추후 정밀화

현재 구현은 데이터 정합성 우선: 늦게 온 과거 캔들이 gap 복구 경로를 통하지 않아도 우선 저장되어 이후 백필/스캐너가 동일 open_time 을 중복 삽입하지 않습니다.

운영 시나리오:
| 상황 | 관측 | 기대 동작 |
|------|------|-----------|
| Late fill 다수 발생 | `kline_late_fill_total` 급증 | 네트워크/소스 스트림 지연 → ingestion lag 원인 분석 |
| 특정 gap 세그먼트가 자연 감소 | (Phase2.5 적용 후) remaining_bars 감소 | Orchestrator 우선순위 자동 재조정 |

### 2. Gap MTTR (Mean Time To Recovery)
`gap_backfill_service.py` 내 세그먼트 완전 복구 시점에 Histogram: `kline_gap_mttr_seconds` 관측.

측정 방식:
- gap dict 의 `detected_ts` (또는 `detected_at`) → time.time() 차이(초)
- datetime 객체/epoch float 모두 허용 (유형 분기 처리)

활용 PromQL 예:
```
histogram_quantile(0.5, sum by (le)(rate(kline_gap_mttr_seconds_bucket[30m])))  # p50 30분 창
histogram_quantile(0.9, sum by (le)(rate(kline_gap_mttr_seconds_bucket[30m])))  # p90
```
알람 가이드(예시):
| 알람 | 조건 | 의미 |
|------|------|------|
| GapMTTRP90Degraded | p90 > 300 (5m 윈도우 2회 연속) | 복구 지연, REST 속도 또는 rate-limit 문제 |
| GapMTTRP50Regression | p50 1h 이동평균 > 180 | 지속적 경미 지연 누적 |

### 3. 365d Continuity 스캐너 (`scripts/jobs/continuity_scan.py`)
CLI 옵션:
```
--symbol SYMBOL
--interval 1m|5m|...
--days 365 (기본)
--json out.json (결과 저장)
--auto-insert-missing (gap_segments 에 누락 세그먼트 자동 insert)
--dsn postgres://... (선택, 미지정 시 설정/환경 변수)
```
출력 예 (표준 출력):
```json
{
  "symbol": "XRPUSDT",
  "interval": "1m",
  "scanned_days": 365,
  "gap_count": 2,
  "gaps": [ {"from_open_time": 1693000000000, "to_open_time": 1693000120000, "missing_bars": 2} ]
}
```
자동 삽입 전략:
- 기존 open/recovered 아닌 세그먼트 중 `from_open_time` 키로 존재하지 않는 항목만 insert
- Orchestrator 주기에서 새로 큐잉되어 백필 경로 진입

Cron 예시(매일 02:10, JSON 결과 보관):
```
10 2 * * * /usr/bin/python /app/scripts/jobs/continuity_scan.py --symbol XRPUSDT --interval 1m --days 365 --json /var/log/continuity/scan_`date +\%Y\%m\%d`.json >> /var/log/continuity/scan.log 2>&1
```
(Windows Task Scheduler 사용 시 동일 인자 적용)

### 4. 운영 Runbook (Phase2 확장)
| 증상 | 조사 지표 | 1차 조치 | 2차 조치 |
|------|----------|---------|---------|
| Late fill 스파이크 | kline_late_fill_total, ingestion lag | 네트워크/WS reconnect | REST 히스토리 sanity fetch (최근 30m) |
| MTTR p90 증가 | kline_gap_mttr_seconds_bucket | 백필 동시성 증가(concurrency) | REST rate-limit/backoff 로깅 추가 |
| Scanner gap 반복 | scan JSON diff | 장기 백필 run 생성 | 외부 공급원 데이터 손실 조사 |
| merge 이벤트 급증 | kline_gap_merge_events_total | 소비 루프 지연 원인 분석 | WS → REST fallback 로직 검토 |

### 5. 로드맵 업데이트
| 항목 | 상태 | 노트 |
|------|------|------|
| Gap merge 정확 missing 재산출 | TODO | DB count 기반 (SELECT COUNT(*) range) 적용 예정 |
| Late fill → gap remaining 조정 | TODO | 세그먼트 분할/감소 + priority 즉시 반영 |
| 365d completeness 게이지 | TODO | 스캐너 내 gauge push 또는 admin endpoint 확장 |
| MTTR 알람 규칙 예시 세트 | Draft | README Alerting 문단 추가 예정 |
| Partial repair 지표 | Planned | `repair` WS 이벤트 카운터 & latency |

### 6. 장애 대응 체크 (요약)
1. Open gap 증가 속도 > 복구 속도? → Orchestrator queue size vs recovered segments
2. MTTR 분포 이동? → p50/p90 비교 (최근 24h 대비)
3. Scanner 지속 동일 gap? → 외부 소스 실제 미발행 구간 가능성 (false gap 여부 재검증)
4. Late fill 많은데 gap 생성 없음? → 지연 도착이지만 연속성 유지 → 모니터링만

---

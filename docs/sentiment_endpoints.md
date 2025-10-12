# Sentiment API와 운영 가이드

이 문서는 감성(Sentiment) 인제션/조회/스트리밍 엔드포인트와 집계/피처 조인/학습 파이프라인, 관측/알림 구성을 정리합니다.

## 엔드포인트 요약
- POST /api/sentiment/tick: 감성 틱 인제션
- GET  /api/sentiment/latest: 최근 스냅샷(EMA/증감/변동성/양성비)
- GET  /api/sentiment/history: 기간 집계(스텝 버킷)
- GET  /stream/sentiment: SSE 스트림(대시보드용)
- GET  /metrics: Prometheus 지표 노출

모든 API는 X-API-Key 헤더가 필요합니다(로컬 무인증 모드는 위험·권장X).

---

## POST /api/sentiment/tick
감성 틱을 저장(중복 upsert)합니다.

본문(JSON):
- symbol: string (예: "XRPUSDT")
- ts: number (epoch ms)
- score_raw: number? (-1.0~1.0 권장)
- score_norm: number? (정규화 점수; 없으면 score_raw 사용)
- count: number? (문서/토큰 수 등)
- provider: string? (소스 라벨)
- meta: object? (선택 메타)

반환: `{ id: number }` 등 저장 결과(구현에 따라 추가 필드 포함).

동작 메모:
- 동일 (symbol, ts, provider) 는 upsert로 병합.
- 점수 클램핑/검증, 중복 윈도우는 환경변수로 제어.

예시(curl):
```
curl -X POST 'http://localhost:8000/api/sentiment/tick' \
  -H 'X-API-Key: <KEY>' -H 'Content-Type: application/json' \
  -d '{"symbol":"XRPUSDT","ts":1690000000000,"score_raw":0.12,"count":7,"provider":"newswire"}'
```

---

## GET /api/sentiment/latest
쿼리:
- symbol: string
- windows: string?  (예: "5m,15m,60m"; 미지정 시 기본값 사용)
- include_meta: bool? (기본 false)

응답 예:
```
{
  "symbol": "XRPUSDT",
  "windows": [5,15,60],
  "snapshot": {
    "ts": 1690000000000,
    "score": 0.12,
    "count": 42,
    "pos_ratio": 0.58,
    "d1": 0.01,
    "d5": -0.03,
    "vol_30": 0.22,
    "ema_5": 0.10,
    "ema_15": 0.08,
    "ema_60": 0.05
  }
}
```

예시(curl):
```
curl 'http://localhost:8000/api/sentiment/latest?symbol=XRPUSDT&windows=5m,15m,60m' -H 'X-API-Key: <KEY>'
```

---

## GET /api/sentiment/history
쿼리:
- symbol: string
- start: number (epoch ms)
- end: number (epoch ms)
- step: string (예: 1m/5m; 기본 `SENTIMENT_STEP_DEFAULT`)
- fields: string? (예: "score,ema_5,ema_15,count,pos_ratio,vol_30")

응답: 시계열 배열(각 항목: {ts, score?, ema_5?, ...}).

메모:
- 서버는 요청 창 내 틱을 스텝 버킷으로 집계(평균/카운트 등) 후 파생(EMA, Δ, 변동성) 계산.
- 최대 포인트 수는 `SENTIMENT_HISTORY_MAX_POINTS` 로 제한.

예시(curl):
```
curl 'http://localhost:8000/api/sentiment/history?symbol=XRPUSDT&start=1689990000000&end=1690000000000&step=1m&fields=score,ema_5,count,pos_ratio' \
  -H 'X-API-Key: <KEY>'
```

---

## GET /stream/sentiment (SSE)
쿼리:
- symbol: string?
- windows: string? (예: 5m,15m,60m)
- step: string? (예: 1m)

이벤트 스트림: 최근 스냅샷과 하트비트(ping). 대시보드용.

예시:
```
# 브라우저 or curl --no-buffer 로 확인 가능
curl --no-buffer 'http://localhost:8000/stream/sentiment?symbol=XRPUSDT&windows=5m,15m,60m&step=1m' -H 'X-API-Key: <KEY>'
```

---

## 환경변수(핵심)
- SENTIMENT_EMA_WINDOWS=5m,15m,60m
- SENTIMENT_STEP_DEFAULT=1m
- SENTIMENT_MAX_LATENCY_MS=600000
- SENTIMENT_DEDUP_WINDOW_SEC=120
- SENTIMENT_PROVIDER_TRUST_JSON={}
- SENTIMENT_ENABLE_SSE=true
- SENTIMENT_HISTORY_MAX_POINTS=10000
- SENTIMENT_POS_THRESHOLD=0.0
- SENTIMENT_AGG_WEIGHTS={}

학습 제어:
- TRAINING_INCLUDE_SENTIMENT=true
- TRAINING_IMPUTE_SENTIMENT=ffill|zero|none
- TRAINING_SENTIMENT_FEATURES=sent_score,sent_cnt,sent_pos_ratio,sent_d1,sent_d5,sent_vol_30,sent_ema_5m,sent_ema_15m,sent_ema_60m
- TRAINING_ABLATION_REPORT=true

---

## 피처 조인과 누수 방지
FeatureService는 캔들 close_time 이하의 감성만 사용합니다(누수 방지). 조인 실패는 스냅샷 실패로 이어지지 않도록 옵셔널 처리되어 있습니다.

관측 지표:
- feature_sentiment_join_attempts_total
- feature_sentiment_join_missing_total → 최근 윈도우로 rate 계산해 누락률 모니터링

---

## 관측/알림 (Prometheus)
지표(일부):
- sentiment_ingest_total, sentiment_ingest_errors_total
- sentiment_ingest_latency_ms_bucket
- sentiment_history_queries_total
- sentiment_history_query_latency_ms_bucket
- sentiment_sse_clients
- sentiment_dedup_upsert_total
- feature_sentiment_join_attempts_total, feature_sentiment_join_missing_total

알림 규칙:
- `infra/prometheus/sentiment_alert_rules.yaml` 를 Prometheus 설정에 rule_files로 포함하세요.

예시 룰:
- SentimentIngestLatencyHighP95
- SentimentIngestErrorsSpike
- SentimentHistoryLatencyHighP95
- SentimentSSEClientsZero
- FeatureSentimentJoinMissingRateHigh

---

## 학습 파이프라인: 감성 포함/어블레이션
- 실행: POST /api/training/run {"sentiment": true}
- ablation: POST /api/training/run {"sentiment": true, "ablate_sentiment": true}
- 결과: /api/training/status?id=... → `sentiment_report.ablation.delta`

지표: AUC, PR-AUC, ECE, Brier, Accuracy 및 Δ.

튜닝 팁:
- 임퓨트 ffill로 시작, 결측이 많은 구간은 zero 사용 고려
- 누락률이 높으면 lookback/윈도우/threshold 조정 또는 인제션 신선도 점검

---

## 트러블슈팅 체크리스트
- 인제션 에러 급증: provider 로그/DB 연결 상태 확인 → 재시도/백오프 설정 검토
- history p95 상승: 조회 범위/스텝 축소, 인덱스 점검
- SSE 0 유지: 프론트/네트워크 점검, CORS/키 확인
- 조인 누락률 상승: 인제션 신선도/윈도우/threshold 재설정, 과도한 downsample 확인
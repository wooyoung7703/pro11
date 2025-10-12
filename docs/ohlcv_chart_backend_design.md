# OHLCV 차트 백엔드 지원 설계 (P1 초안)

## 1. 범위 (P1)
- 실시간 Partial 캔들 라이프사이클
- Delta 동기화 (최근 N개 변경 빠른 패치)
- Gap 탐지/노출 + Repair 반영 이벤트
- 기본 Aggregation (1m 원본 -> 5m, 15m, 1h on-demand) 첫 단계
- WebSocket 이벤트 확장

## 2. 데이터 모델 확장 (DB 변경 없음)
- Canonical 1m 테이블 그대로 사용 (symbol, ts, o,h,l,c,v, closed, repaired, partial_flag 없음)
- Partial 은 메모리 Layer (in-flight bucket) 로 유지 후 close 시 DB flush (기존 flush 경로 재사용) 가능 구조로 설계
  - P1 에서는 실제 flush 경로 변경 없이 WS 로만 partial 을 표현 (DB에는 닫힌 시점만 기록)

## 3. WebSocket 이벤트 스펙
공통 envelope:
```
{
  "type": "snapshot|append|repair|partial_update|partial_close|gap_detected|gap_repaired|aggregation_append",
  "symbol": "BTCUSDT",
  "interval": "1m",          // aggregation_append 는 요청된 target interval
  "server_time": 1733234567890 // ms
  "payload": { ... }
}
```

### 3.1 snapshot (기존)
- payload: { candles: [...], meta: { from, to, count, completeness_percent, largest_gap } }

### 3.2 append (기존)
- payload: { candle }

### 3.3 repair (기존)
- payload: { candle, reason: "backfill|manual" }

### 3.4 partial_update (신규)
```
payload: {
  "ts": 1733234520000,     // 캔들 시작(분) epoch ms
  "open": 12345.0,
  "high": 12360.0,
  "low": 12310.0,
  "close": 12355.5,
  "volume": 12.345,
  "progress": 0.42,        // (현재 시각 - ts)/(60s) 0~1 범위
  "include_open": true
}
```

### 3.5 partial_close (신규)
- 해당 ts 가 확정 (DB flush 직후 append 와 동일한 final 과의 차이: final 로 잠금)
```
payload: { "ts": ..., "final": { o,h,l,c,v }, "latency_ms": 320 }
```
- 클라이언트 처리 규칙:
  1. partial bucket 제거
  2. final candle 로 교체(append 와 동일 index)

### 3.6 gap_detected (신규)
```
payload: {
  "from_ts": 1733234400000,
  "to_ts": 1733234460000,
  "missing": 6,           // 분 단위 캔들 개수
  "largest_gap": 6,
  "completeness_percent": 98.7
}
```

### 3.7 gap_repaired (신규)
```
payload: {
  "range": { "from_ts": ..., "to_ts": ...},
  "candles": [ ...closed candles... ],
  "largest_gap": 0,
  "completeness_percent": 100
}
```

### 3.8 aggregation_append (신규)
- 서버 측 on-demand aggregate 계산 결과(예: 5m) 신규 바/업데이트.
```
payload: {
  "interval": "5m",
  "candle": { ts, o,h,l,c,v, source_count: 5 }
}
```

## 4. REST 신규 엔드포인트
1. GET /api/ohlcv/delta?symbol=BTCUSDT&since=epoch_ms&limit=500
   - 반환: { base_from, base_to, candles: [...], repairs: [...], truncated: bool }
   - candles: since 이후 새로 추가(append) 확정 캔들
   - repairs: { ts, candle } 목록
   - partial 은 포함하지 않음 (WS 로만)

2. GET /api/ohlcv/gaps/status?symbol=BTCUSDT
   - (이미 존재 가정) 확장: segments 필드 추가
   - segments: [{ from_ts, to_ts, missing, state: "open|repairing|closed" }]

3. GET /api/ohlcv/aggregate?symbol=BTCUSDT&interval=5m&limit=300
   - 서버 즉시 집계(슬라이딩) → 캐시 (LRU + interval+symbol+last_ts)
   - 반환: { interval: "5m", candles: [...] }

## 5. Partial 처리 흐름 (P1 단순화)
- Ingestion loop 에서 새 분 시작 감지 시 partial bucket 생성 (메모리 dict: key=ts)
- 틱/트레이드 업데이트 -> partial bucket 갱신 -> debounce(최소 500ms 간격) 로 partial_update broadcast
- 분 종료(다음 분 flush 직후) → 기존 append broadcast 직전 partial_close broadcast 후 append 유지 or append 생략?
  - 선택: append 유지 (클라이언트 일관성; partial_close 받은 뒤 append 중복 시 클라이언트는 덮어쓰기 허용)
  - 최적화(P2): partial_close 가 final candle 포함하도록 하고 append 생략.

## 6. Gap 탐지
- 현재 completeness 계산 로직 확장: flush 후 직전 구간 missing scan
- 새 gap 발견 → gap_detected 즉시 브로드캐스트
- repair 작업(백필) 완료 → gap_repaired + repair 이벤트 각 캔들별 또는 batch (P1: batch 포함 candles 배열 1회)

## 7. Aggregation 전략 (초기)
- 요청 시 in-memory 캐시 미스 -> DB range fetch (1m) -> groupBy interval, 계산 -> 캐시 저장 (key: symbol:interval:latest_source_ts)
- 새 append(1m) 발생 시 해당 symbol 에 대한 캐시된 interval 들 중 마지막 incomplete aggregate 업데이트 가능 여부 판단 후 즉시 aggregation_append 브로드캐스트 (옵션 플래그로 토글)
- P1: 5m,15m,1h 고정 세트만 허용

## 8. 성능 및 부하 제어
- partial_update 최소 전송 주기: 500ms (ENV: OHLCV_PARTIAL_MIN_PERIOD_MS)
- delta REST limit 기본 500, 최대 2000
- aggregation 캐시 만료: 마지막 source_ts 갱신 후 2분

## 9. 에러/경계 케이스
- since 가 base_to 이후: candles=[] repairs=[] truncated=false
- 너무 오래된 since (보관 범위 미포함): 400 + guidance
- gap_detected 와 동시에 이미 repair 된 race: gap_repaired 단일 전송 (중복 suppress)

## 10. 메트릭 추가
- ohlcv_partial_updates_sent_total
- ohlcv_partial_close_sent_total
- ohlcv_gap_detected_total / ohlcv_gap_repaired_total
- ohlcv_delta_requests_total{truncated=bool}
- ohlcv_aggregation_cache_hits / _misses

## 11. 테스트 시나리오 요약
1) partial 모의 → partial_update 이벤트 수신 & progress 증가
2) partial 종료 → partial_close 이후 append 수신 (동일 ts) → 최종 값 일치
3) delta: since 이전 append + repair 조합 검증
4) gap: missing range 강제 삭제 후 status 및 gap_detected 이벤트
5) aggregation: 5m 요청 후 새 1m append 5번째 도달 시 aggregation_append 수신

## 12. 점진적 마이그레이션
- 클라이언트: 기존 snapshot + append/repair 그대로 유지 → 신규 타입 graceful ignore 후 점차 UI 구현
- 문서화: docs/ohlcv_ws_protocol.md 별도 작성 예정

## 13. P2 예고(문서에는 개요만)
- snapshot delta 압축
- partial batch frame (다중 symbol)
- historical backfill streaming

---
초안 완료. 다음 단계: WS 프로토콜 문서 분리 + 구현 착수.

# OHLCV 실시간 스트리밍 전략 (P1)

## 1. 목표
- Partial → Close 전환을 일관된 순서로 전송
- 불필요한 이벤트 폭주 억제(backpressure)
- 재연결(reconnect) 시 데이터 일관성 확보
- Gap/Repair, Delta 와의 상호작용 정의

## 2. 이벤트 순서 규칙 (Invariants)
1. 동일 ts 에 대해 순서: [0..n회 partial_update] → (partial_close) → (append) → (optionally repair later)
2. partial_close 와 append 의 ts 동일. (P1: append 유지, P2: append 생략 가능)
3. gap_detected 는 해당 missing range 첫 발견 시 1회.
4. gap_repaired 는 해당 range 복구 완료 시 1회. repair 개별 candle broadcast(기존)와 공존.
5. 서버 clock 기준 server_time 단조 증가 (역행 금지).

## 3. 서버 사이드 전송 정책
| 항목 | 정책 | ENV | 기본값 |
|------|------|-----|--------|
| partial 최소 주기 | 마지막 전송 후 min_period_ms 이전이면 스킵 | OHLCV_PARTIAL_MIN_PERIOD_MS | 500 |
| partial coalesce | 주기 동안 변경 누적 후 마지막 스냅샷 1회 전송 | 고정 | - |
| 최대 누락 감시 윈도우 | gap 탐지 스캔 깊이(분) | OHLCV_GAP_SCAN_WINDOW_MIN | 240 |
| delta limit max | delta REST 최대 반환 | OHLCV_DELTA_MAX_LIMIT | 2000 |
| aggregation intervals | 허용 집계 세트 | OHLCV_AGG_INTERVALS | "5m,15m,1h" |

### 3.1 Partial Coalescing
- Ingestion 틱 수신 시 partial bucket 갱신.
- 전송 스케줄러(타이머): now - last_partial_sent >= min_period ⇒ 현재 snapshot push.
- 진입 직후(새 분 시작) 첫 틱도 즉시 전송(초기 open 시각 표시) 예외 허용.

### 3.2 Partial Close
- 분 경계 도달(+약간의 flush latency) 후:
  1) partial_close (latency_ms 포함)
  2) append (final candle)
- latency_ms = close_flush_time - (ts + 60_000)

## 4. 재연결 시퀀스 (Client)
1. WS 끊김 감지 → reconnect 시도.
2. 최근 확정 바 마지막 ts = L (partial 제외).
3. REST /api/ohlcv/delta?since=L 호출.
4. delta 응답 처리 (candles/repairs 반영).
5. /api/ohlcv/gaps/status 호출 → gaps 세트 동기화.
6. 새 WS 구독 완료.
7. 이후 들어오는 snapshot 은 무시(선택적으로 disable snapshot 요청 헤더 추가 고려 P2).

Race 방지: delta 처리 완료 전 WS append/repair 도착 가능 → WS 큐 임시 버퍼링 후 delta 완료 시 적용.

## 5. Backpressure & 보호
| 상황 | 대응 |
|------|------|
| partial 업데이트 과다(틱 폭주) | min_period 필터 + 마지막 상태만 유지 |
| 큐 적체(네트워크 지연) | partial 은 최신만 유지(older drop), append/repair 는 모두 순차 전송 |
| 클라이언트 렌더 느림 | partial frame skip (프론트 측 rAF 기반 마지막만 반영) |

## 6. 클라이언트 처리 파이프라인
```
Raw WS Event → Validate(type, schema) → Dispatch by type → State Mutation → Chart Adapter(diff apply)
```
우선순위: partial_close > repair > append > partial_update
(같은 tick 프레임에서 충돌 시 위 순서 적용)

## 7. 상태 전이 (Partial)
| 현재 상태 | 이벤트 | 다음 상태 | 동작 |
|-----------|--------|----------|------|
| none | partial_update | partial(ts) | temp bar add/update |
| partial(ts) | partial_update(ts) | partial(ts) | 값/진행률 갱신 |
| partial(ts) | partial_close(ts) | none | temp 제거 + final 교체 준비 |
| none | append(ts) | none | candles push |

## 8. Gap / Repair 상호작용
- gap_detected: gaps 목록 추가; 차트는 구간 표시.
- repair: 해당 ts 교체 후, gap 구간이 완전히 채워졌는지 검사 → 채워짐: gap_repaired 적용(segments state=closed).
- gap_repaired: segments 업데이트 + 필요 시 completeness UI 갱신.

## 9. Delta 계약
```
GET /api/ohlcv/delta?symbol=BTCUSDT&since=TS&limit=500
{
  "base_from": 1733232000000,
  "base_to": 1733235600000,
  "candles": [...],
  "repairs": [{ "ts":..., "candle":{...}}],
  "truncated": false
}
```
에러: since < base_from → 400 + { "error":"since_out_of_range","hint":"request snapshot" }

## 10. 모니터링 (서버)
- Counter: ohlcv_partial_updates_coalesced_total
- Gauge: ohlcv_partial_last_latency_ms (현재 마지막 partial 전송 간격 / 필요 시 제거 예정)
- Histogram: ohlcv_partial_close_latency_ms (분 경계 대비 partial_close 브로드캐스트 지연; 버킷: 50~5000ms)
- Gauge: ohlcv_partial_close_last_latency_ms (최근 1회 partial_close latency 관측값)
- Counter: ohlcv_ws_events_sent_total{type}
- Counter: ohlcv_delta_requests_total{truncated}

설명:
| 메트릭 | 목적 | 주요 해석 포인트 |
|--------|------|------------------|
| ohlcv_partial_close_latency_ms | flush pipeline 지연 모니터 | 장기적으로 1s 이상 빈도 증가 시 ingestion/backfill/DB I/O 확인 |
| ohlcv_partial_close_last_latency_ms | 최근 latency 트렌드 빠른 파악 | 알람: >1500ms 단 3회 연속 시 경고 |
| ohlcv_delta_requests_total{truncated} | delta 응답 집합 truncation 비율 | truncated=true 비율이 높으면 limit 상향/클라이언트 주기 조정 |
| ohlcv_ws_events_sent_total{type} | 이벤트 볼륨/비중 분석 | partial_update:append 비율 급증 시 backpressure 정책 재점검 |

예시 PromQL:
```
histogram_quantile(0.95, sum(rate(ohlcv_partial_close_latency_ms_bucket[5m])) by (le))
increase(ohlcv_delta_requests_total{truncated="true"}[10m])
sum by (type) (rate(ohlcv_ws_events_sent_total[1m]))
```

## 11. 장애/이상 케이스
| 케이스 | 원인 | 대응 |
|--------|------|------|
| partial_close 누락 | flush 예외 | watchdog: 분 종료 + 2s 경과 & partial 존재 ⇒ 강제 close/append 재전송 |
| append 중복 | 재시도 로직 | idempotent: ts 기반 replace(append 이전에 존재하면 교체) |
| repair 직후 또 repair | 외부 backfill 중복 | 마지막 repair ts 저장 후 동일 payload hash 무시 |
| snapshot 과 delta 중첩 | 재연결 레이스 | snapshot 수신 시 delta 이미 처리 여부 플래그 검증 |

## 12. 구현 체크리스트 (서버)
상태: 2025-10-03 기준 P1 핵심 구현 완료. gap 이벤트는 현재 broadcast 메서드/메트릭 존재, 실제 gap 탐지 트리거는 ingestion 훅 연결 예정.

- [x] partial bucket 구조 + scheduler (coalescing 로직 포함)
- [x] debounce/coalesce 로직 (min period 기반, 마지막 스냅샷만 전송)
- [x] partial_close broadcast 삽입 (latency 계산 필드 포함 예정)
- [x] delta 엔드포인트 (/api/ohlcv/delta, truncated 플래그, limit 적용)
- [x] gap_detected/gap_repaired broadcast 스텁 & 메트릭
- [x] 메트릭 등록 (partial/gap/delta counters)
- [x] ENV 파라미터 주입 (OHLCV_PARTIAL_MIN_PERIOD_MS, OHLCV_DELTA_MAX_LIMIT 등)
- [ ] gap 탐지 실제 로직 연결 (ingestion 후 누락 스캔) ← 남은 항목

추가 예정(P1 마무리): delta repairs 배열 실제 채움 (현재 빈 배열 반환)

## 13. 구현 체크리스트 (프론트)
현재: WebSocket composable (useOhlcvWs) 1차 스켈레톤 생성(자동 재연결, 이벤트 타입 분기, partial preview 적용). 아직 delta/gap 상태 동기화 및 레이스 버퍼 미구현.

- [x] useOhlcvWs 기본 연결/재연결/이벤트 분기
- [x] partial state 적용 + temp 바 preview (partial_update → 진행; partial_close → 확정)
- [ ] 이벤트 큐 + delta 선처리 버퍼 (재연결 레이스 회피)
- [ ] delta 동기화 함수(fetchDelta) + 마지막 확정 close_time 기준 호출
- [ ] gap/status fetch 및 store 반영 (segments 시각화 데이터)
- [ ] partial_close latency 측정 로그 출력
- [ ] race 버퍼 flush 순서 보장 로직
- [ ] 성능 계측(logging) 토글 (옵션화)

## 14. 성능 타겟
- Partial 업데이트 ≤ 2 fps (기본 500ms 간격)
- Append 처리 → 차트 반영까지 50ms 내
- Reconnect 복구(Delta + gaps) 1초 이내 (네트워크 왕복 제외)

## 15. 향후(P2) 확장 포인트
- partial batch (다중 symbol → 하나의 events 배열)
- delta 압축(연속 candle run-length)
- gap progressive repair streaming

## 16. 변경 이력
| 날짜 | 내용 |
|------|------|
| 2025-09-30 | 초기 초안 작성 |
| 2025-10-03 | 서버 P1 구현 상태 체크리스트 갱신, 프론트 WS composable 생성 반영, 남은 gap 탐지 로직/ delta repairs TODO 명시 |

## 17. WS ↔ Delta 인터셉트 통합 설계 메모
재연결 또는 일시적 네트워크 단절 재복구 시 데이터 일관성을 보장하기 위해 WS 실시간 스트림과 REST delta 호출 사이 레이스를 다음 정책으로 다룬다.

### 17.1 목표
- Delta 처리 전에 도착한 append/repair 이벤트 손실/이중 적용 방지
- Partial 이벤트는 delta 복구 범위(확정 캔들)와 무관하므로 단순 discard 혹은 최신 1개만 유지
- 구현 복잡도 최소화 (P1 메모리 버퍼)

### 17.2 컴포넌트
- useOhlcvWs: 순수 WS 수신/분배 (append/repair/partial_* 등)
- useOhlcvDeltaSync: delta 실행 및 레이스 버퍼 (append/repair 가 delta 완료 전 도착 시 버퍼링)
- ohlcv store: 단일 truth source (candles, gapSegments)

### 17.3 상태 플래그
| 플래그 | 소스 | 의미 |
|--------|------|------|
| syncing | useOhlcvDeltaSync | delta 진행 중 여부 (true ⇒ append/repair 버퍼링) |
| lastDeltaSince | useOhlcvDeltaSync | 마지막 delta base open_time (확정 기준) |
| reconnectAttempts | useOhlcvWs | 백오프 재연결 지표 |

### 17.4 이벤트 흐름 (재연결 시퀀스)
1. WS 연결 (autoConnect=false) 상태에서 먼저 snapshot 수신 or 기존 candles 유지
2. deltaSync.runDelta() 호출 (syncing=true)
3. runDelta 동안 WS append/repair 수신 ⇒ intercept* 훅이 버퍼에 push
4. runDelta 완료 ⇒ new candles/repairs 적용 후 wsBuffer flush (open_time ASC 정렬)
5. syncing=false 전환 ⇒ 이후 append/repair 즉시 반영

### 17.5 인터셉트 훅 통합
useOhlcvWs 내부 onAppend/onRepair 직전에 콜백 배열 실행:
```
for cb in interceptors.append: if cb(candles): return  # 소비되면 기본 처리 스킵
```
DeltaSync 가 등록하는 cb 는 syncing 중이면 true 반환 (버퍼링 완료) / 아니면 false (통과).

### 17.6 Partial 처리 전략
- partial_update: syncing 여부와 무관하게 실시간 미리보기 유지 (snapshot 이후 차트 공백 방지)
- partial_close: latency 측정 후 확정; delta 로 확정 바 다시 중복되지 않도록 동일 open_time 교체(idempotent)

### 17.7 Repair 우선순위
- Flush 시 순서: repairs → appends? or appends → repairs?
  - 선택: appends 먼저, repairs 나중에 재정렬. 이유: 동일 open_time 충돌 시 repair 가 최종 정확 값 덮어쓰기.

### 17.8 경계/에지 케이스
| 케이스 | 대응 |
|--------|------|
| delta 호출 실패(HTTP 에러) | 재시도 (지수 backoff) 또는 full snapshot 강제 (fallback 모드) |
| 버퍼 폭주 (장시간 syncing) | 상한 (예: 500 events) 초과 시 oldest drop + 경고 로그 |
| repair 와 append 동일 open_time | repair 최종 덮어쓰기 (applyRepair 후 정렬) |
| partial_close 직후 delta | delta 는 since 기준으로 partial_close 확정 open_time 포함 안 됨 (since=lastClosed) |

### 17.9 추후(P2) 확장
- 버퍼 persistent (IndexedDB) → 탭 리프레시 후 레이스 복구
- 이벤트 압축 (append run-length 또는 diff 리스트)
- Snapshot disable 플래그 (delta 기반 cold start)

### 17.10 구현 체크리스트 (통합)
- [x] useOhlcvDeltaSync 버퍼 구조(runDelta, flushBuffer)
- [ ] useOhlcvWs append/repair 인터셉터 등록 포인트 노출
- [ ] 버퍼 상한 + 드롭 카운터 (appliedPartialDrops 등 세분화)
- [ ] full snapshot fallback 분기

해당 설계에 따라 P1 최소 기능 이후 P2 리팩토링 시 useOhlcvWs 에 인터셉터 배열을 노출하는 경량 수정이 필요하다.

---
초안 완료. 다음 단계: 인디케이터 모듈 설계.

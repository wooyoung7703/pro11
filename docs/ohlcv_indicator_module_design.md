# OHLCV 테크니컬 인디케이터 모듈 설계 (P1)

## 1. 목표
- 클라이언트(프론트) 측 계산 중심: 저지연, 서버 부하 최소화
- append / repair 이벤트에 대응하는 증분(recompute 최소화) 인디케이터 업데이트
- 추후(P2) 서버 프리컴퓨트 + delta push 전환 용이한 구조

## 2. 1차 지원 지표
| 지표 | 사용 값 | 파라미터 | 복잡도 | 증분 업데이트 |
|------|---------|----------|--------|---------------|
| SMA  | close   | period N | O(1) (슬라이딩 합) | 이동 합계 갱신 |
| EMA  | close   | period N, multiplier | O(1) | prevEMA 이용 |
| RSI  | close   | period N | O(1) | avgGain/avgLoss 누적 |
| ATR  | high/low/close | period N | O(1) | prevATR + TR 교체 |

추가 후보(P2): MACD, Bollinger Bands, VWAP, OBV.

## 3. 데이터 구조 제안
```
interface Candle { ts:number; o:number; h:number; l:number; c:number; v:number }
interface IndicatorSeriesPoint { ts:number; value:number }
interface IndicatorCacheKey { symbol:string; interval:string; name:string; paramsHash:string }

interface SMAState { period:number; windowSum:number; queue:number[] }
interface EMAState { period:number; k:number; last?:number }
interface RSIState { period:number; avgGain?:number; avgLoss?:number; lastClose?:number }
interface ATRState { period:number; lastATR?:number; trQueue:number[]; trSum:number }
```
- 캐시 맵: Map<IndicatorCacheKey, { points: IndicatorSeriesPoint[]; state: SMAState|EMAState|...; lastTs:number }>
- paramsHash: JSON.stringify(params) sha1 or 단순 `${period}` 문자열 (P1 단순화)

## 4. 계산 파이프라인 (append)
1. 새 확정 candle c
2. 활성화된 모든 인디케이터 키 iterate
3. 해당 state 증분 함수 호출(update(state, c))
4. 결과 value 반환 시 points push
5. Lightweight Charts 에 대응 series.update({ time: c.ts/1000, value })

## 5. 초기 로딩 (snapshot 이후)
- candles 배열 순회하며 차례대로 update → warmup.
- RSI/ATR 등 초기 period 충족 전 value=null 로 skip (또는 NaN → 시각화 제외).

## 6. Repair 처리
1. repair 로 특정 ts 의 candle 교체
2. 영향 구간 계산:
   - SMA: 해당 ts 이상 마지막 N-1 개 재계산 필요 (단순: ts 이후 전체 재계산; P1 허용)
   - EMA: 교체 ts 이후 전체 재계산 (EMA 연쇄 영향)
   - RSI: 교체 ts 포함 이후 전체 재계산
   - ATR: 교체 ts 및 이후 period 영향 (TR 의존)
3. 최적화 단계(P2): 구간 제한 재계산 구현

P1 단순 전략: repair 발생 시 해당 인디케이터 시리즈 clear 후 snapshot 이후부터 재적재 (성능 허용 범위 확인). 분당 수백 repair 미만 가정.

## 7. Partial 반영 여부
- Partial 캔들은 인디케이터 확정에 포함하지 않음 (닫힐 때만)
- 옵션: 실시간 preview 모드(부분 EMA 등) P2 고려

## 8. API (프론트 내부 유틸)
```
registerIndicator(symbol, interval, name, params) => IndicatorHandle
unregisterIndicator(handle)
updateOnAppend(candle)
recomputeAll(symbol, interval) // repair 후 호출
getSeries(name) => points[]
```

## 9. 예시: EMA 업데이트
```
function updateEMA(state:EMAState, close:number): number | null {
  if (!state.last) { state.last = close; return close; }
  const ema = (close - state.last) * state.k + state.last;
  state.last = ema;
  return ema;
}
```

## 10. 성능 추정
- 활성 지표 수 K, append 빈도 f(분당 1) 가정.
- O(K) per append, 각 지표 O(1) → 총 O(K).
- Repair 전체 재계산: O(K * C) (C = candle 수) → 일평균 캔들 1440, K=4 → 5,760 연산 (수 ms 수준 예상)

## 11. 메트릭(선택)
- indicator_recompute_full_total{name}
- indicator_points_total{name}
- indicator_repair_recalc_ms_sum{name}

## 12. 에러/경계
| 케이스 | 동작 |
|--------|------|
| period > candles 길이 | value null 유지 |
| NaN close 값 | 해당 캔들 무시(로그) |
| repair 연속 다수 | debounce 100ms 후 단일 재계산 |

## 13. 단계별 구현 순서
1) 자료구조/인터페이스 선언(ts 모듈) `indicators/engine.ts`
2) SMA/EMA 증분 구현 + 등록/해제
3) RSI/ATR 추가
4) Repair 재계산(풀 리빌드) 함수
5) Store 통합 (append/repair hook 연결)
6) Chart series 생성/업데이트 바인딩
7) 성능 로깅(옵션)

## 14. P2 확장
- 서버측 프리컴퓨트 + /api/ohlcv/indicators?since=...
- Lazy window trim (points > Nmax 시 앞부분 drop)
- GPU/WebWorker 계산 (대량 multi-symbol)

---
초안 완료. 다음 단계: 차트 테스트/품질 전략 문서화.

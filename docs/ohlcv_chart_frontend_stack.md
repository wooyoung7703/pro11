# OHLCV 차트 프론트엔드 기술 스택 결정

## 1. 평가 목표
P1 범위(Partial 실시간, Gap/Repair 표시, Delta 패치, 기본 Aggregation, 인디케이터 초기)에서 빠른 구현 + 낮은 유지보수 비용 + 고성능을 달성할 라이브러리 선정.

## 2. 후보군
| 라이브러리 | 장점 | 단점 | 라이선스 | Partial/Gaps 구현 난이도 | 인디케이터 확장성 | 번들/성능 |
|-----------|------|------|----------|--------------------------|--------------------|-----------|
| Lightweight Charts | 경량, 빠름, 캔들/볼륨 기본, Vue 통합 쉬움 | 고급 드로잉 부족, UI 도구 직접 구현 필요 | Apache 2.0 | 매우 쉬움 (update) | 지표 직접 계산 후 setData | 매우 우수 |
| TradingView Charting Library (Full) | 풍부한 도구/인디케이터/드로잉 | 라이선스/통합 복잡, 무거움 | 비공개(허가 필요) | 쉬움 (엔진 내), 커스터마이즈 제약 존재 | 내장 많음(과잉 가능) | 상대적으로 무거움 |
| ECharts | 다목적 강력, 커스터마이징 다양 | 금융 특화 최적화 부족, 실시간 빈번 업데이트 부담 | Apache 2.0 | 중간 (series 직접 관리) | 계산 직접 필요 | 중간 ~ 무거움 |
| Recharts | React 친화, 문서 친절 | Vue 아님, 성능 한계(빈번 업데이트) | MIT | 어려움 (구조적) | 계산 직접 필요 | 중간/재렌더 비용 큼 |
| uPlot | 초경량 고성능 라인 | 캔들/부가기능 수작업 | MIT | 중간 (커스텀 레이어) | 직접 구현 | 최고 (라인 기준) |

## 3. 평가 기준 상세
1. 실시간 성능: 초당 수회 partial_update + append 처리 시 60fps 근접 유지.
2. 개발 효율: Vue 3 + TypeScript 도입 난이도 낮음.
3. 표현 유연성: partial 진행 상태(progress), gap 영역 마킹, repair 강조.
4. 인디케이터 전략: 초기에 서버 계산 없이 클라이언트 계산 후 캐시 가능.
5. 라이선스 제약 최소화: 상업/폐쇄 배포 가능.
6. 미래 확장: 다중 심볼, 멀티 인터벌 overlay.

## 4. 최종 선정: Lightweight Charts
### 선택 근거
- 실시간 업데이트(재할당/append) API 심플 → partial_update/partial_close 패턴 구현 용이.
- 낮은 러닝커브: 초기 차트 도입 1일 내 MVP 가능.
- 번들 경량 → 프론트 초기 로딩 지연 최소화.
- Gap/Repair: 마커(Series price line, time scale marker) + background region (툴킷 없으므로 P1은 마커/라인, P2 커스텀 캔버스 layer 고려).
- 라이선스 자유 → 배포/상용 문제 없음.
- 커뮤니티/예시 풍부.

### 보완 계획(필요 기능 미제공 영역)
| 요구 | 보완 방안 |
|------|-----------|
| 드로잉 도구(추세선 등) | P2: 경량 커스텀 캔버스 오버레이 또는 별도 라이브러리 결합 |
| 다중 패널(인디케이터 분리) | 동일 라이브러리 다중 chart instance + 동기 스크롤 |
| 고급 인디케이터(볼린저, MACD) | 내부 계산 유틸(ts 모듈) 작성 후 series 추가 |
| Gap 영역 시각 | timeScale coordinate 추출 후 plugin-like overlay (canvas) |

## 5. 기본 통합 구조
```
frontend/src/
  composables/
    useOhlcvWs.ts        # WS 연결 및 이벤트 디멀티플렉서
  components/
    OhlcvLiveChart.vue   # Lightweight Charts 초기화 / 업데이트
  stores/
    ohlcv.ts             # state: candles, partial, gaps, repairs, indicators
```

## 6. 상태 모델 제안
```
interface Candle { ts:number; o:number; h:number; l:number; c:number; v:number; }
interface PartialState { ts:number; o:number; h:number; l:number; c:number; v:number; progress:number; }
interface GapSegment { from_ts:number; to_ts:number; missing:number; state:'open'|'repairing'|'closed'; }
```
- candles: 확정 바(정렬: ts ASC)
- partial: 진행 중 0 또는 1개 (P1 단일 심볼)
- gaps: UI overlay + 목록 표시
- repairs: 최근 n개(알림/하이라이트용)

## 7. 이벤트 매핑 규칙
| WS type | 처리 | 차트 반영 |
|---------|------|-----------|
| snapshot | candles 세팅 & meta 기록 | setData |
| append | candle push/update | update/append |
| repair | index 찾기 후 교체 + 강조 상태 | series.update + highlight queue |
| partial_update | partial state merge | temp bar update (series.update) |
| partial_close | partial 제거 + final 반영 | series.update (final) |
| gap_detected | gaps push/merge | overlay 마커 표시 |
| gap_repaired | gaps 상태 전환 + candles batch update | 재도색/refresh |
| aggregation_append | 별도 interval store/series | 보조 차트 또는 탭 |

## 8. 인디케이터 초기 전략
- 클라이언트 계산: SMA/EMA (O(n)) 슬라이딩 윈도우.
- 지표 캐시 키: symbol:interval:length:last_ts
- append/repair 시 영향 받은 trailing window 만 재계산.

## 9. Reconnect & Delta
- WebSocket reconnect → 마지막 확정 ts(L) 저장 → GET /api/ohlcv/delta?since=L 호출 → candles/repairs 반영 → gaps/status 요청 → WS 재동기화.
- 실패/HTTP 4xx 시 풀 snapshot 폴백.

## 10. 배포/번들 고려
- Dynamic import(코드 스플릿)로 차트 페이지 최초 접근 시 로드.
- Lightweight Charts tree-shaking 가능. 빌드 후 차트 관련 청크 사이즈 측정 목표 < 100KB gzip.

## 11. 단계별 도입 (P1 세분화)
1) 패키지 설치 및 기본 차트 렌더 (정적 데이터를 setData)
2) snapshot + append 반영
3) partial_update + partial_close 반영
4) reconnect delta 적용
5) gap_detected / gap_repaired overlay
6) repair highlight (색 or 마커)
7) aggregation_append 분리 탭 / 토글
8) SMA/EMA 두 개 지표 추가

## 12. 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| 과도한 partial 업데이트 빈도 | CPU/GC 증가 | 서버 side 최소 주기(500ms) + 클라이언트 debounced apply |
| 대량 repair batch | UI 렌더 스파이크 | requestAnimationFrame chunked 적용 (P2) |
| Gap overlay 좌표 오류 | 시각 혼란 | 단위 테스트 + timeScale convert 테스트 |
| Reconnect race (snapshot 이전 delta) | 데이터 불일치 | 단계: snapshot 먼저 -> delta since last snapshot_to |

## 13. 메트릭 (프론트 자체 수집 제안)
- ws_event_counts{type}
- ws_latency_ms (partial_close.latency_ms 히스토그램)
- chart_fps_estimate (rAF 측정, 개발 모드 콘솔)

## 14. 결정 요약
Lightweight Charts 채택. TradingView Full 은 향후 고급 기능 필요 시 세컨더리 옵션.

---
문서 완료. 다음 Todo: 실시간 스트리밍 전략 세부 설계(P1 서버 전송 정책 & 클라이언트 처리 순서).
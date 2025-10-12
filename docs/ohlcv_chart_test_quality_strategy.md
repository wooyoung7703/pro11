# OHLCV 차트 테스트 & 품질 전략 (P1)

## 1. 목표 품질 속성
| 속성 | 목표 | 계측 지표 |
|------|------|-----------|
| 정확성 | append/repair/partial 이벤트 반영 일관성 | 시나리오 테스트 결과 | 
| 복구성 | WS 끊김 후 1초 내 상태 동기화 | reconnect_latency_ms | 
| 성능(FPS) | Idle 55~60fps, partial 시 50fps 이상 | rAF 샘플링 | 
| 지연(latency) | partial_close→시각 반영 ≤ 80ms | latency_ms 히스토그램 | 
| 일관성 | delta + WS race 무손실 | diff 검증 로그 | 

## 2. 테스트 계층
| 계층 | 도구 | 목적 |
|------|------|------|
| 단위(Unit) | Vitest | 인디케이터 계산(SMA/EMA/RSI/ATR) 정확성 |
| 스토어(State) | Vitest + 가짜 이벤트 | partial/append/repair 처리 순서 검사 |
| 통합(WS+Store) | Playwright or Node ws client | 실제 WS 서버 이벤트 흐름 검증 |
| E2E(UI) | Playwright | 렌더, 시각 요소(Gap 마커) 존재/갱신 |
| 퍼포먼스(프로파일) | Lighthouse + custom script | 초기 로딩 번들 사이즈, FPS, GC 체크 |

## 3. 핵심 시나리오
1. Partial Lifecycle
   - partial_update 다수 → progress 증가 단조성 → partial_close → 동일 ts append → 최종 값 동일
2. Gap Repair
   - delete_offset_range 로 gap 유도 → gap_detected 수신 → mock_repair → gap_repaired + repair 이벤트 반영 후 completeness 증가
3. Reconnect Delta
   - WS 강제 종료 → delta fetch → WS 재연결 → 누락 이벤트 없음 (candles 길이/해시 비교)
4. Indicator Recompute
   - append 연속 N개 후 SMA 마지막 값 수학적 기대값 일치
   - repair 후 재계산: EMA 마지막 값 두 재계산 경로 동일
5. Aggregation Append
   - 5m 대상 1m 5개 누적 후 aggregation_append 수신 및 올바른 H/L/V 계산
6. Latency Measurement
   - partial_close.latency_ms 수집 → 평균/최대 임계 내(알람 범위 정의)

## 4. 계측(Instrumentation) 포인트
| 포인트 | 방법 |
|--------|------|
| partial_close→렌더 | 이벤트 처리 시간 기록 + nextTick 이후 performance.now 비교 |
| reconnect_latency | 재연결 시작~delta+gaps 처리 완료 시각 차이 |
| ws_event_counts | 내부 카운터 + flush 시 console(debug) |
| series_points_count | candles/indicators 길이 검사 |

## 5. 도구 설정 초안
- 패키지: vite + vitest + @testing-library/vue + playwright
- CI 단계: lint → unit(store & indicators) → integration(ws mock) → e2e (선택 flag)
- Test id 정책: data-test="ohlcv-gap-marker", data-test="ohlcv-partial-bar"

## 6. Mock & Fixture 전략
| 대상 | 방법 |
|------|------|
| WS 이벤트 | 메모리 WebSocket 서버 (ws 패키지) + enqueue script |
| Delta 응답 | 고정 JSON fixture + since/limit 분기 |
| Gap/Repair | mock_delete + mock_repair 호출 시퀀스 래퍼 |
| Snapshot | 정렬된 1m 캔들 fixture 생성기 (기간/seed 기반) |

## 7. 성능 테스트(경량)
- script: 1,000 candles snapshot + 60 partial_update (500ms) + 60 append
- 측정: 평균 render frame drop (<5%), 메모리 증가 < 10MB

## 8. 실패 패턴 & 감지
| 패턴 | 지표/검증 |
|------|-----------|
| 중복 append | candles 배열 마지막 2개 ts 동일 → fail |
| out-of-order | ts 역행 발생 시 fail |
| partial_close 누락 | partial 존재 & 다음 append ts 증가 → 경고 |
| delta 손실 | 재연결 후 expected_count != actual_count → fail |

## 9. 리포트 포맷
- JSON: { suite, passed, failed, duration_ms, metrics: { latency_p95, reconnect_avg_ms } }
- CI 업로드(추후 Grafana 연동 고려)

## 10. 위험 & 완화
| 위험 | 설명 | 완화 |
|------|------|------|
| 실시간 변동성 높은 장세 | partial 폭주 | 서버 min_period 조정 테스트 포함 |
| 대형 repair batch 지연 | UI 프리즈 | chunk apply 테스트 케이스 마련(P2) |
| 타임존/시간대 오류 | ts 변환 실수 | 모든 테스트 UTC epoch 기준 assert |

## 11. P1 완료 정의(Definition of Done)
- 최소 시나리오(1~4) 전부 green
- latency 측정 harness 동작 & 기본 수치 로깅
- 문서화(README 차트 섹션) 테스트 실행 가이드 포함

## 12. 향후(P2)
- WebWorker 지표 계산 테스트
- 스트레스 테스트 (10k candles + fast partial 5fps)
- Visual regression (chromatic-like 스냅샷) 적용

---
초안 완료. 모든 설계 문서 완성 후 구현 단계로 전환 준비 완료.

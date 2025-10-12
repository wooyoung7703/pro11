# 뉴스 관측성 / 메트릭 확장 제안

## 1. 현재 수집/부족 지표
이미 존재(추정): sentiment cache hit/miss (백엔드), 전역 시스템 Prometheus 기본 지표.
부족:
- 뉴스 수집 지연(ingestion lag)
- 프론트 fetch 실패/재시도율
- Delta fetch 적용율 및 절감된 payload 크기
- Highlight/필터 파이프라인 재계산 비용 (프론트)
- Pagination / Cursor 사용 분포
- Body lazy fetch 성공/실패 및 평균 batch 크기
- ETag 304 비율 (조건부 응답 효과)

## 2. 정의(용어)
- Ingestion Lag: 기사 published_ts 와 시스템 DB insert_ts (또는 first_seen_ts) 차이(ms)
- Freshness: 최근 5분 내 신규 기사 수 / 총 기사 수 비율
- Delta Efficiency: (전체 fetch bytes - delta fetch bytes) / 전체 fetch bytes 누적 평균

## 3. 백엔드 메트릭 설계
| 메트릭 | 타입 | 라벨 | 설명 |
|--------|------|------|------|
| news_ingestion_lag_ms | Histogram | source | published→ingested 지연 분포 |
| news_freshness_ratio | Gauge |  | 최근 5분 신규/전체 기사 비율 |
| news_recent_requests_total | Counter | delta(bool), summary_only(bool) | recent API 호출 수 |
| news_recent_rows_returned_sum | Counter | delta(bool) | 반환 행 수 누적 |
| news_recent_payload_bytes_sum | Counter | delta(bool) | 직렬화 응답 byte 합 |
| news_recent_cache_hit_total | Counter |  | 쿼리 캐시 적중 |
| news_recent_cache_miss_total | Counter |  | 쿼리 캐시 미스 |
| news_recent_etag_not_modified_total | Counter |  | 304 응답 횟수 |
| news_sentiment_windows_computed_total | Counter | windows(count) | 다중 window sentiment 계산 호출 |
| news_body_batch_fetch_total | Counter | batch_size(bucket) | body bulk fetch 발생 수 |
| news_body_batch_rows_sum | Counter |  | batch 로 받은 본문 row 합 |
| news_body_single_fallback_total | Counter |  | batch 실패 후 단건 fallback |

## 4. 프론트 측 지표 (Web Vitals + Custom)
수집 방법: PerformanceObserver + beacon (5~10초 또는 unload 시)
| 지표 | 형식 | 설명 |
|------|------|------|
| news_frontend_pipeline_recalc_total | Counter | 필터/정렬/하이라이트 단계별 재계산 카운트 (label=stage) |
| news_frontend_highlight_ms | Histogram | highlight 전체 실행 시간 |
| news_frontend_delta_applied_total | Counter | delta fetch merge 성공 |
| news_frontend_delta_skipped_total | Counter | 중복/무변경으로 skip |
| news_frontend_payload_bytes_sum | Counter | 실제 받은 응답 크기 (압축 해제 후) |
| news_frontend_body_batch_request_total | Counter | body batch 요청 횟수 |
| news_frontend_body_batch_avg_size | Gauge | 직전 N회 평균 batch 크기 |
| news_frontend_fetch_error_total | Counter | fetch 실패 (label=phase:recent|body|status) |
| news_frontend_retry_total | Counter | 재시도 실행 수 |

## 5. 알림 (Alerting) 제안
| 이름 | 조건 | 심각도 |
|------|------|--------|
| HighIngestionLag | p95(news_ingestion_lag_ms) > 30000 5분 지속 | warning |
| FreshnessDrop | news_freshness_ratio < 0.05 10분 | critical |
| DeltaIneffective | 1h delta 모드 비율 < 0.5 | info |
| ETagLowReuse | etag_not_modified_total / requests_total < 0.1 (6h) | info |
| BodyBatchFailures | body_single_fallback_total > 0 (최근 10분) | warning |
| FrontFetchErrors | fetch_error_total 증가율 > 50% (전 30분 대비) | critical |

## 6. 구현 순서
1) 백엔드 recent API 파트 지표 삽입 (requests/rows/payload/etag)  
2) ingestion lag 계산 (insert 시 now - published_ts 저장)  
3) 캐시 hit/miss 및 multi-window sentiment 카운트  
4) 프론트 성능/파이프라인 지표 skeleton + beacon 업로드 endpoint (`/api/metrics/fe-news`)  
5) Alert rule 예시(yaml) 문서화  

## 7. 예시 코드 스니펫 (백엔드)
```python
from prometheus_client import Counter, Histogram, Gauge

news_recent_requests_total = Counter(
  'news_recent_requests_total', 'Recent API calls', ['delta','summary_only']
)
news_recent_latency_seconds = Histogram(
  'news_recent_latency_seconds', 'Recent API latency'
)

# 사용 예
start = time.perf_counter()
# ... 처리 ...
news_recent_requests_total.labels(delta=str(bool(since_ts)).lower(), summary_only=str(summary_only).lower()).inc()
news_recent_latency_seconds.observe(time.perf_counter()-start)
```

## 8. 데이터 최소화 / 개인정보 고려
- 기사 데이터는 공개 뉴스 → PII 없음
- 프론트 beacon 에 사용자 식별자 포함 금지 (세션 익명 UUID 24h 롤링)

## 9. DoD
- 대시보드(백엔드 + 프론트)에서: ingestion lag, delta 비율, ETag 재사용, pipeline 재계산, highlight latency 한 화면 시각화
- 24h 메트릭 유지로 일변동 분석 가능

(초안 v1)

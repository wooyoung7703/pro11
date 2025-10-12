# 뉴스 모듈 UX 개선 제안

## 1. 현황 요약
- 검색 토큰 입력 UX: 공백/구두점 분리 규칙 직관성 부족
- 토큰 비활성(Disable) 상태가 새로고침 시 초기화
- 긴 목록 스크롤 시 맥락 유지 어려움 (날짜 구분자, 현재 필터 요약 없음)
- Sentiment 가 숫자/색만으로 표현되어 미세한 변화 추적 곤란
- 본문 펼침 후 접기 UX 불명확 (접기 버튼/아이콘 일관성 부족 가능성)

## 2. 목표
| 항목 | 개선 지표 |
|------|-----------|
| 검색 토큰 인지성 | 잘못된 입력 비율 감소 |
| 상태 지속성 | 새로고침 후 동일 뷰 재현 |
| 스크롤 탐색성 | 기사 탐색 평균 시간 단축 |
| Sentiment 가독성 | 중간값/변화 추세 직관화 |
| 본문 인터랙션 | 펼침/접기 전환 명확성 |

## 3. 개선 아이템 상세
### 3.1 검색 토큰 UX
- 입력 힌트 placeholder: 예) "쉼표 또는 스페이스로 여러 키워드" 
- 잘못된 빈 토큰 필터링 전 사용자에게 inline chip preview
- 최근 사용 키워드 (localStorage) 드롭다운 자동완성

### 3.2 토큰 비활성 상태 지속성
- disabledTokens 배열을 localStorage(`news.disabledTokens.v1`) 직렬화
- 페이지 진입 시 복원, API 키 바뀌거나 APP_ENV 달라지면 네임스페이스 분리

### 3.3 필터 요약 바 (Sticky)
- 상단 고정 바에 현재: 활성 토큰 수 / 비활성 토큰 수 / 검색어 / 날짜 범위 / 기사 수
- 변경 발생 시 300ms debounce 반영 → 재렌더 최소화

### 3.4 날짜/섹션 구분자
- 일자 경계마다 Divider 삽입 (YYYY-MM-DD) + collapse toggle (해당 날짜 기사 숨김)

### 3.5 Sentiment 시각화 개선
- 색상 + 작은 sparklines (최근 3개 window sentiment 평균) 
- Tooltip: raw score, rank percentile
- Extreme 구간(상위/하위 5%) Badge 표시

### 3.6 본문 펼침/접기 명확화
- Chevron 아이콘 회전 애니메이션 150ms
- 키보드 접근성: Enter/Space 지원 + aria-expanded
- 펼친 상태에서 ESC 로 접기

### 3.7 Infinite Scroll (Pagination 연계)
- 백엔드 cursor 기반 `?cursor=<ts>&limit=...` 형식 준비되면 하단 sentinel (IntersectionObserver) 로 추가 로드
- Loading spinner → Skeleton 3개로 대체

### 3.8 Quick Actions 바
- "모두 접기", "하이라이트 끄기", "새로운 n개 보기" (delta fetch 후) 버튼

### 3.9 접근성 (A11y)
- 대비(contrast) 검증; sentiment 색상 WCAG AA 준수 팔레트
- 스크린리더용 숨김 텍스트: 기사 sentiment 설명 (예: "긍정 0.72 (상위 20%)")

## 4. 우선순위
1) 상태 지속성 (disabledTokens + 최근 키워드)  
2) 필터 요약 바 + 날짜 Divider  
3) Sentiment sparklines  
4) Infinite scroll  
5) Quick Actions  
6) A11y 향상  

## 5. 데이터/백엔드 연계 요구
- Multi-window sentiment API (예: windows=5,15,30) → sparklines 집계
- Cursor pagination + delta fetch → infinite scroll 기반

## 6. 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| LocalStorage 손상 | 복원 실패 | try-catch + 초기화 로그 | 
| Sparklines 과도한 DOM | 성능 저하 | 캔버스 batch 렌더 or SVG reuse | 
| Infinite scroll 중복 로드 | 데이터 중복 | in-flight flag + cursor 비교 |
| 색상 팔레트 미적합 | 가독성 저하 | contrast checker 사전 적용 |

## 7. 완료 정의 (DoD)
- 새로고침 전후 토큰/비활성 상태 100% 일치
- 1000개 기사 환경에서 scroll FPS ≥ 55 유지 (Chrome 60fps 기준)
- Sentiment sparkline hover 시 tooltip 정보 표시

## 8. 성능 & 관측 (Backend/Frontend 구현 현황)
### 8.1 Delta Fetch (`since_ts`)
- `/api/news/recent?since_ts=<ts>` 호출 시 해당 시각(초/밀리초 자동판별) 이후 신규 기사만 반환
- 응답 필드 `delta: true` 표시, `count` 가 0 이어도 최신성 확인 용도
- 프론트 스토어: `lastLatestTs` 를 유지하며 증분 머지 → 전체 재요청 대비 네트워크 감소

### 8.2 Summary Only 경량화
- 기본 `summary_only=1` 에서 body 제외 후 lightweight summary(title 앞 80자 + …) 서버 측 삽입
- 초기 목록 로딩 payload 감소 → body 필요 시 확장 시점 지연 로드

### 8.3 Batch Body Fetch
- `/api/news/body?ids=1,2,3` 최대 200개 본문 동시 조회
- 프론트 expand 시 개별 fetch 대신 큐에 id 모아서 debounce (예: 50~80ms) 후 일괄 호출
- N개의 연속 확장 → N회 → 1회 수준으로 HTTP 감소

### 8.4 ETag + 304 재검증
- 캐시 키: 파라미터 (limit,symbol,since_ts,summary_flag) + 상위 id 시그니처
- 동일 조건 재호출 시 `If-None-Match` 헤더와 매칭 → 변경 없으면 304, 본문 미전송
- 메트릭: `news_recent_not_modified_total` 으로 재검증 효율 추적 (304 비율 ↑ == 캐시 적중 ↑)

### 8.5 Rate Limiting (모드 분리)
- full(first load) vs delta(fetch after since_ts) 서로 다른 버킷 (기본 full=10/min, delta=60/min 환경변수로 조정)
- 메트릭: `news_recent_rate_limited_total{mode}` 로 남용/비정상 패턴 감지
- 목표: delta 비중 ↑, full 제한 통해 과도한 전체 재호출 억제

### 8.6 In-Memory LRU Cache
- 최근 요청 파라미터 조합 최대 128개 보관, ETag 재사용 및 직렬화 비용 절감
- LRU 밖으로 밀려난 항목은 자연회수 → 메모리 사용 상한 유지

### 8.7 Prometheus 메트릭 요약
| Metric | 설명 | 라벨 |
|--------|------|------|
| `news_recent_requests_total` | /api/news/recent 호출 수 | delta, summary_only |
| `news_recent_rows_returned_total` | 반환 기사 행 누적 | delta |
| `news_recent_payload_bytes_total` | 직렬화된 응답 바이트 (추정) | delta |
| `news_recent_latency_seconds` | 처리 지연 히스토그램 | - |
| `news_recent_not_modified_total` | 304 Not Modified 응답 횟수 | - |
| `news_recent_rate_limited_total` | 429 발생 횟수 | mode(full|delta) |
| `news_sentiment_cache_hits_total` | sentiment window 캐시 히트 | - |
| `news_sentiment_cache_miss_total` | sentiment window 캐시 미스 | - |

### 8.8 프론트 하이라이트/필터 최적화
- 복수 키워드 단일 Combined RegExp 로 병합 → N번 테스트 → 1회 실행 패턴
- 필터/하이라이트 결과 memoization(LRU 유사) → 동일 파라미터 연속 재계산 방지

### 8.9 확장 가능성 (차후)
| 제안 | 개요 | 기대효과 |
|------|------|---------|
| 304 비율 게이지 | (304 / total) 실시간 계산 Exporter 혹은 기록 | 캐시 건강도 대시보드화 |
| Payload 감축 추적 | full vs delta 평균 bytes 비교 | 네트워크 비용 개선 가시화 |
| Pre-Warm Delta | 마지막 폴 이후 빈 delta 연속 시 폴링 후 즉시 diff push | 체감 신선도 ↑ |
| Cursor Pagination | `next_cursor` 기반 무한 스크롤 API | UX/메모리 개선 |

### 8.10 테스트 인메모리 모드
- 환경변수 `NEWS_TEST_INMEMORY=1` 설정 시 DB 없이 NewsRepository 가 메모리 리스트를 사용
- insert 시 hash 중복 방지, published_ts DESC 정렬 유지
- CI / 로컬 빠른 유닛 테스트에 적합 (asyncpg / Postgres 불필요)
- sentiment window 집계 또한 메모리 데이터 대상으로 동일 로직 적용

### 8.11 비율 게이지 추가
| Gauge | 정의 | 산출 방식 |
|-------|------|-----------|
| `news_recent_not_modified_ratio` | 304 비율 (0~1) | not_modified / total_calls (프로세스 누적) |
| `news_recent_delta_usage_ratio` | delta 호출 비중 (0~1) | delta_calls / total_calls |

주의: 현재는 프로세스 lifetime 누적 기반이므로 재시작 시 초기화. 필요 시 추후 슬라이딩 윈도우(예: 최근 15분) 구현 가능.

### 8.12 Payload 평균 및 절감 비율 게이지
| Gauge | 의미 | 계산 | 비고 |
|-------|------|------|------|
| `news_recent_payload_delta_avg_bytes` | delta 요청 평균 payload 크기 | Σ delta bytes / delta 호출 수 | 프로세스 누적 기반 |
| `news_recent_payload_full_avg_bytes` | full 요청 평균 payload 크기 | Σ full bytes / full 호출 수 | 프로세스 누적 기반 |
| `news_recent_delta_payload_saving_ratio` | delta 절감 비율 | 1 - (delta_avg / full_avg) | full_avg>0 일 때 0~1 클램프 |

예시 Prometheus/Grafana 쿼리:
- 최근 6h 절감 비율 추세 (Gauge 그대로 시계열): `news_recent_delta_payload_saving_ratio`
- Δ1h full 평균: `increase(news_recent_payload_bytes_total{delta="false"}[1h]) / (increase(news_recent_requests_total{delta="false"}[1h]) > 0)`
- Δ1h delta 평균: `increase(news_recent_payload_bytes_total{delta="true"}[1h]) / (increase(news_recent_requests_total{delta="true"}[1h]) > 0)`

시나리오 해석:
- saving_ratio 가 0.70 ⇒ delta 평균 payload 가 full 평균의 30% 수준 (70% 절감)
- saving_ratio 급락 ⇒ delta가 과도한 body 포함(요청 파라미터 오류) 혹은 full 호출 증가 가능성 조사

## 9. 측정 지표 설정 제안
- 304 비율 목표: 초기 <10% → 최적화 후 40%+ (활발한 delta 사용 가정)
- 평균 payload 절감: full 대비 delta 호출 평균 bytes 70% 이하
- Rate limit(429) 발생률: < 1% (정상 사용 시) / spike 탐지 알람 룰 구성

## 10. 운영 대시보드 구성 초안
1) 요청수 vs 304 vs 429 스택 차트
2) Rows Returned p95 / Latency p95
3) Payload Bytes (full vs delta 구분)
4) Sentiment Cache 히트율 (hit / (hit+miss))
5) Delta 비중: delta 호출수 / 전체 호출수

(초안 v1, 추가 v2)

## 11. 실데이터 RSS 수집 전환 (New)
### 11.1 구조 개요
이전: 단일 Synthetic 생성기 → 랜덤 기사 생성 (데모/부하 테스트용)
현재: 플러그형 Fetcher 아키텍처 (실서비스는 RSS만 사용)
- `RssFetcher`: 개별 RSS URL 파싱(feedparser) → 표준화 dict(title, url, published_ts, hash, ...)
- (SyntheticFetcher: 과거 데모용, 현재 생산 경로에서 제거)
- `NewsService.fetchers`: 다중 fetcher 순회, 결과 집계 후 bulk insert

### 11.2 환경변수
| 변수 | 예시 | 설명 |
|------|------|------|
| `NEWS_RSS_FEEDS` | `https://example.com/feed,https://news.site/rss` | 콤마 구분 RSS URL 목록 (공백 trim) |
| (Deprecated) `NEWS_SYNTH_FALLBACK` | 제거됨 | synthetic 자동 추가 기능 삭제 |
| `NEWS_TEST_INMEMORY` | `1` | 테스트 시 DB 대신 메모리 저장소 사용 |

### 11.3 Hash 전략 (중복 방지)
`hash = sha256(f"{url}|{published_ts}|{title}")[:48]`
- URL + 게시 시각 + 제목 결합으로 title-only 대비 충돌 감소
- RSS 항목에 body 가 없더라도 안정적 식별자 확보
- published_ts 누락 시 현재 시각 fallback → 동일 제목 반복시 (시간차 존재) 구분 가능

### 11.4 Per-Source 메트릭 (신규)
| Metric | Type | Label | 의미 |
|--------|------|-------|------|
| `news_source_fetch_latency_seconds` | Histogram | source | 개별 RSS(또는 synthetic) fetch 지연 분포 |
| `news_source_articles_total` | Counter | source | fetch 1회에서 산출된 원시 기사 수 (insert 전) |

활용:
- Raw vs Ingested 비율 = `sum(rate(news_articles_ingested_total[5m])) / sum(rate(news_source_articles_total[5m]))` (중복/필터 수준 추정)
- Latency p95 상승 감지: 특정 소스 feed 장애 조기 경보

### 11.5 오류/복원 시나리오
- feedparser 예외 발생: 해당 fetcher만 경고 로깅 + error counter 증가, 다른 소스 지속
- (기존 fallback 제거됨) RSS 전체 실패 시 신규 기사 없음 + health score 하락 경고

### 11.6 테스트 (Mock RSS)
신규 테스트: `test_news_rss_fetcher_mock.py`
- `feedparser.parse` monkeypatch → 고정 entries 반환
- 정상 경로: refresh → recent 호출 → Alpha/Beta 제목 확인, 메트릭 노출 검증
- 오류 경로: parse 예외 시도 → refresh 성공(0개) + latency 히스토그램/오류 카운터 존재 확인

### 11.7 Grafana 확장 제안
추가 패널:
1) Per-source fetch latency (heatmap 혹은 top-N bar p95)
2) Raw vs Ingested ratio (line)
3) Source volume stacked (articles_total rate by source)

### 11.8 향후 확장
- Content enrichment: 본문 추출(é.g. Readability) + 지연 증가 대비 별도 워커 분리
- Multi-language 감지 (lang 필드 자동 탐지) + 번역 파이프라인
- Duplicate near-similarity: 제목/요약 cosine sim > threshold 시 병합
- Backfill 잡: 초기 기동 시 최근 N 시간 RSS 히스토릭 ingest (rate limit 고려)

### 11.9 운영 체크리스트
| 점검 | 기준 |
|------|------|
| Latency p95 | < 2s (외부 feed 변동 제외) |
| Raw→Inserted 비율 | > 0.6 (너무 낮으면 중복 과다 또는 parsing 오류) |
| Empty delta streak | 지속 증가 시 실제 신규 기사 부재 or fetch 실패 |
| Saving ratio | 0.5~0.85 (delta 최적화 정상) |

### 11.10 장애 패턴 예시
| 패턴 | 관측 | 추정 원인 | 대응 |
|------|------|-----------|------|
| 특정 source latency 급등 | 해당 source 히스토그램 p95 상승 | 외부 서버 지연 | 임시 제외 / backoff |
| Raw 증가, inserted 정체 | raw counter spike, ingested flat | hash 실패 또는 중복 feed | hash 로깅/필드 재검증 |
| delta empty streak 증가 + raw=0 | 새 기사 미수집 | 전체 feed 정체 | 수동 확인/대체 소스 추가 |

### 11.11 Dedup / 회복탄력성 메트릭 (v2 추가)
최근 추가된 실시간 운용 & 중복 억제 관련 메트릭:

| Metric | Type | Labels | 의미 | 활용 |
|--------|------|--------|------|------|
| `news_source_backoff_skips_total` | Counter | source | Backoff 남은 시간 때문에 fetch 시도 생략 횟수 | 특정 소스 장애/지연 지속도 파악 (빈도 증가 시 feed 불안정) |
| `news_source_disabled_skips_total` | Counter | source | 운영자가 disable 한 소스로 인한 skip | 운영 중 수동 차단 현황 추적 |
| `news_source_dedup_dropped_total` | Counter | source | 메모리/세션 수준 hash 중복으로 drop 된 기사 수 | 과다 중복 여부 (source feed 품질) |
| `news_source_dedup_inserts_total` | Counter | source | 세션 내 unique 로 유지된 기사 수 | 원시 대비 실제 유효 기사 흐름 |
| `news_source_dedup_ratio` | Gauge | source | unique/raw (프로세스 lifetime 근사) | 장기 중복 효율 추적 |
| `news_source_dedup_ratio_rolling` | Gauge | source, window | 최근 N poll 롤링 unique/raw | 단기 변동 감지 (갑작스런 0 또는 1 수렴) |
| `news_ingest_raw_to_inserted_ratio` | Gauge | - | 전체 inserted / (raw sum) 근사 | 전체 파이프 dedup 효율 |
| `news_dedup_hash_preload_count` | Gauge | - | 재시작 시 파일로부터 preload 된 hash 수 | 초기 중복 억제 워밍업 효과 확인 |
| `news_source_health_score` | Gauge | source | 합성 건강 점수 (0~1) | 우선 모니터/알람 대상 자동 랭킹 |
| `news_source_last_ingest_timestamp` | Gauge | source | 마지막 unique 기사 ingest 시각(Unix) | 정체 시간 계산 기반 |
| `news_source_stalled` | Gauge | source | 정체 여부 (1=stalled) | 장기 공급 중단 즉시 감지 |
| `news_source_stall_events_total` | Counter | source,event | stall/recover 전이 카운트 | 플랩 억제(전이 기반 알람) |

#### Health Score 계산(초기 버전)
가중치/휴리스틱(변경 가능):
- Latency: 2초 이하면 1.0, 2~10초 사이 선형 감소 → 최소 0
- Dedup Rolling Ratio: 0.2~0.95 사이면 건강대 (0.9 근사), 너무 낮거나(중복 과다) 너무 높을 때(변화 없음/정지 의심) 감점
- Backoff 활성: 잔여 backoff 비율 기반 최대 0.7 패널티
- 해당 poll 에러 발생 시 추가 0.3 패널티
 - Stalled: `since_last_ingest > NEWS_SOURCE_STALL_THRESHOLD_SEC` 이면 0.5 패널티 (공급 정체 강등)
 - Base = 0.5*latency + 0.25*dedup + 0.25 (bias)
 - 최종 score = clamp(base - (backoff_pen + err_pen + stall_pen))

향후 개선 아이디어:
1) Latency p95/mean 이력 기반 지수 완충(EWMA) 적용 → 단발 스파이크 완화
2) Dedup score 를 “신규 기사 공급 속도” (raw rate) 와 결합하여 “정지(feed stalled)” 의사결정 강화
3) Backoff 남은 시간 vs 누적 backoff 비중(rolling 1h) 분리하여 chronic vs transient 구분
4) Error 분류 (네트워크, 파싱, HTTP 상태 코드)별 가중치 차등

#### Persistent Hash Buffer
재시작 직후 과거 해시 정보 부재로 인한 초기 duplicate insert 시도 폭주를 줄이기 위해 최근 해시를 파일에 저장 후 재기동 시 로딩.

환경변수:
| 변수 | 기본 | 설명 |
|------|------|------|
| `NEWS_HASH_BUFFER_PATH` | `news_hash_buffer.txt` | 최근 해시를 줄 단위로 저장할 파일 경로 |
| `NEWS_HASH_BUFFER_LIMIT` | `5000` | 메모리/파일에 유지하는 최대 최근 해시 수 (FIFO) |
| `NEWS_SOURCE_STALL_THRESHOLD_SEC` | `900` | 소스별 마지막 unique ingest 이후 이 초를 넘으면 stalled 간주 |

동작:
1) 서비스 시작 시 파일 존재하면 최대 LIMIT 만큼 읽어 세트 초기화 → `news_dedup_hash_preload_count` 기록
2) poll 후 실제 DB insert 성공한 unique 기사 hash 추가
3) LIMIT 초과 시 가장 오래된 것부터 제거 후 파일 전체 overwrite (소형 크기 전제)
4) 실패/IO 예외는 무시 (필요 시 향후 에러 카운터 추가 가능)

장점:
- 롤링 dedup 세트 seed → restart 후 첫 몇 회 poll 중복률 급상승 억제
- Unique constraint 예외/로그 감소

Trade-off & 주의:
- 멀티 프로세스(여러 Uvicorn 워커) 환경에서는 파일 경합 가능 → 파일락 또는 중앙 캐시(예: Redis set) 권장
- 해시 포맷 변경 시 (hash 전략 바뀌면) 이전 파일은 무시하거나 버전 prefix 도입 고려
- 현재는 전체 덮어쓰기(O(size)) → LIMIT 값 커질 경우 append + 주기적 compaction 전략으로 변경 가능

#### 모니터링 패턴 해석 예시
| 관측 | 해석 | 액션 |
|------|------|------|
| backoff_skips 급증 + health_score <0.3 | 해당 feed 연속 오류/지연 | 임시 disable 후 원인 조사 |
| dedup_ratio_rolling → 0 근접 | 반복 중복 (feed loop / 캐시 문제) | feed URL/콘텐츠 diff 점검 |
| dedup_ratio_rolling → 1 고정 + raw=0 | 신규 기사 공급 정지 | 대체 소스 추가 / 장애 알림 |
| hash_preload_count 낮음(0 또는 급감) | 파일 손상/경로 변경 가능 | 파일 경로/권한 확인 |
| health_score 전반 하락(복수 feed) | 외부 네트워크/공용 인프라 문제 | 공용 네트워크/방화벽/latency 측정 |

#### 향후 (Roadmap)
- health_score 구성 요소/가중치 Prometheus export (내부 디버그 gauge) → explainability
- 최근 N분 rolling 윈도우 기반 dedup ratio 별도 gauge (`*_rolling_time`) 시간 가중치 반영
- Persistent hash buffer 를 Redis probabilistic 구조(Bloom)로 확장 → 메모리 절약 + false positive 허용

### 11.12 Feed Health Snapshot API (신규)
프론트에서 실시간 피드 상태 시각화를 위해 경량 스냅샷 엔드포인트 제공:

`GET /api/news/feeds/health`

응답 예시:
```json
{
	"status": "ok",
	"feeds": [
		{"source":"news.example.com","score":0.82,"disabled":false,"backoff_remaining":0,
		 "components": {"latency_seconds":1.2,"lat_score":0.94,"dedup_ratio":0.63,"dedup_score":0.9,
			 "backoff_pen":0.0,"err_pen":0.0,"base_pre_penalty":0.67,"final_score":0.82}},
		{"source":"rss.other.net","score":0.41,"disabled":false,"backoff_remaining":12.3},
		{"source":"legacy.feed","score":null,"disabled":true,"backoff_remaining":0}
	]
}
```

필드 설명:
| 필드 | 의미 |
|------|------|
| source | RSS (또는 synthetic) 소스 식별자 |
| score | 0~1 건강 점수 (계산 전/없음이면 null) |
| disabled | 런타임 disable 여부 |
| backoff_remaining | 남은 backoff 초 (0이면 즉시 fetch 가능) |
| components.latency_seconds | 해당 poll latency (마지막 관측) |
| components.lat_score | latency 2초 기준 스케일링 점수 |
| components.dedup_ratio | 롤링 unique/raw 비율 |
| components.dedup_score | dedup_ratio 기반 가공 점수 |
| components.backoff_pen | backoff 패널티 (0~0.7) |
| components.err_pen | poll 중 오류 발생 시 0.3 패널티 |
| components.base_pre_penalty | (0.5*lat +0.3*dedup +0.2) 원천 합산 값 |
| components.final_score | 최종 score (clamp 후) |

UI 권장 매핑:
- score >=0.8: green, 0.6~0.8: teal/라이트그린, 0.4~0.6: amber, 0.2~0.4: light red, <0.2: red
- disabled=true -> 회색 처리 + hover 툴팁
- backoff_remaining>0 -> amber badge (카운트다운)

알람 초기 기준(제안):
| 조건 | 1차 경보 | 2차 (지속) |
|------|----------|-----------|
| score < 0.3 | 5분 내 3회 이상 | 30분 이상 연속 |
| backoff_remaining 반복 | 10분 동안 5회 이상 재진입 | 1시간내 30% 이상 backoff 상태 |

### 11.13 Health Event 메트릭
Threshold (기본 0.3, `NEWS_HEALTH_THRESHOLD` 조정 가능)을 기준으로 상태 전환 발생 시 이벤트 카운터 증가:

| Metric | Type | Labels | 의미 |
|--------|------|--------|------|
| `news_source_health_events_total` | Counter | source, event(drop|recover) | 건강 임계치 하강 / 회복 전환 발생 횟수 |

활용 예시 (최근 1h 전환 빈도 Top N):
```
topk(5, increase(news_source_health_events_total[1h]))
```
회복율 모니터링 (drop 대비 recover 비율):
```
sum by (source) (increase(news_source_health_events_total{event="recover"}[6h])) /
	clamp_min(sum by (source) (increase(news_source_health_events_total{event="drop"}[6h])), 1)
```
비율 < 0.5 이면 회복 지연 의심.

### 11.14 Dedup Poll History & Sparkline (신규)
중복 억제 품질의 단기 추세를 UI에서 직관적으로 보기 위해 per-poll 히스토리 엔드포인트와 스파크라인 도입.

`GET /api/news/feeds/dedup_history`

응답:
```json
{
	"status":"ok",
	"history": {
		"rss.example.com": [
			{"ts":1727840123.12, "raw":5, "kept":3, "ratio":0.6},
			{"ts":1727840215.47, "raw":4, "kept":4, "ratio":1.0}
		],
		"another.feed": [
			{"ts":1727840125.01, "raw":2, "kept":1, "ratio":0.5}
		]
	}
}
```

특징:
- 서버 `NewsService` 내부 `_dedup_history` deque: 최근 N회 poll (환경변수 `NEWS_DEDUP_ROLLING_WINDOW`) 만큼 유지.
- 각 poll 에 대한 raw / kept / ratio (unique/raw) 기록.
- 프론트는 소스별 ratio 배열을 0~1 범위로 정규화하여 inline SVG polyline 스파크라인 렌더.

탐지 패턴 예시:
| 패턴 | 스파크라인 모양 | 해석 | 대응 |
|------|----------------|------|------|
| 갑작스런 0 근처 급락 | 고원 → 바닥 | 동일/중복 기사 폭증 | feed loop / hash 입력값 재검증 |
| 지속 1.0 평평 | 새 기사 거의 없음 (변화 없음) | feed 정체 또는 소스 다운 | 대체/점검 |
| 톱니형 0.3~0.9 변동 | 정상 범위 | 자연스러운 신규/중복 혼합 | 모니터만 |

Grafana 보강:
`increase(news_source_dedup_inserts_total[15m]) / increase(news_source_articles_total[15m])` 와 sparkline 동시 비교하여 서버 vs UI 불일치 감지.

추가 개선(미래):
1) Time-based window (분 단위) 히스토리 별도 유지 → poll 간격 변화에도 일관된 시간 분해능.
2) Ratio 급변 이벤트 Counter (예: 직전 대비 |Δratio| > 0.5).
3) 스파크라인 hover 시 raw/kept tooltip.

프론트 폴링 주기: 기존 status/delta 주기(10s)와 합쳐 동일 간격 fetch → 추가 부하 경미.



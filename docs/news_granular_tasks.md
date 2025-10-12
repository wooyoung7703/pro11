# 뉴스 모듈 개선 Granular Tasks

## 1. Backend - Phase 1 (High Impact)
1. recent endpoint: since_ts + summary_only 파라미터 추가
2. recent endpoint: response schema 통일 (items[], delta flag)
3. recent endpoint: Prometheus 지표(news_recent_requests_total 등) 등록
4. sentiment multi-window 계산 함수 작성 (cache reuse)
5. recent endpoint: windows 파라미터 지원 + sentiment dict 구성
6. recent endpoint: cursor 페이지네이션 (next_cursor encode/decode util)
7. recent endpoint: ETag 생성 + If-None-Match 처리
8. recent endpoint: in-memory query cache (TTL 5s, LRU 128)
9. recent endpoint: tokens/sentiment_range server-side filter
10. body bulk fetch endpoint (/api/news/body?ids=) 추가

## 2. Backend - Phase 2 (Reliability & Security)
11. HTML Sanitization (strip disallowed tags) util + 적용
12. Rate limit rules (delta/full 구분) + 429 처리
13. Abuse detection (IP error ratio) in-memory
14. Metrics: ingestion lag 계산 (insert hook)
15. Metrics: freshness ratio gauge 업데이트 job (매 60s)
16. Metrics: news_sentiment_windows_computed_total 추가

## 3. Frontend - Phase 1 (Data Layer)
17. store: delta fetch (since_ts) 로직 추가 (마지막 timestamp 추적)
18. store: mergeNewArticles 함수 (중복 id skip + 정렬 유지)
19. store: summaryOnly 모드 초기 로드 + body lazy fetch 전환
20. store: AbortController in-flight 관리
21. store: backoff & retry 유틸 (지수 + jitter)
22. store: batch body fetch queue + debounce 구현

## 4. Frontend - Phase 2 (Performance)
23. 파이프라인 메모화 레이어 (signature hash)
24. Combined highlight regex 컴파일러
25. Virtual scroller 도입 (라이브러리 선택 및 wrapper 컴포넌트)
26. highlight 비동기 처리 (requestIdleCallback 또는 worker) 선택적

## 5. Frontend - Phase 3 (UX)
27. disabledTokens localStorage 지속성 구현
28. 최근 검색 토큰 저장 + 자동완성 드롭다운
29. 필터 요약 Sticky Bar 컴포넌트
30. 날짜 Divider + collapse state 유지
31. sentiment sparkline 컴포넌트 (multi-window 활용)
32. Quick Actions Bar (모두 접기 등)
33. A11y 개선(aria-expanded, contrast palette)

## 6. Frontend - Phase 4 (Reliability & Security)
34. sanitize 적용 후 v-html 제거 검증
35. regex 길이/토큰 검증 UI 사전차단
36. delta fetch race 방지 in-flight key 적용 테스트
37. localStorage schema 검증 + state reset metric beacon
38. sentiment 실패 fallback 배지/tooltip

## 7. Observability
39. FE beacon POST /api/metrics/fe-news 엔드포인트 (익명 세션)
40. FE metrics 집계 serializer (batch flush 10s)
41. Dashboard panel: ingestion lag / delta ratio / etag reuse / highlight p95

## 8. Testing
42. Backend: recent since_ts unit/integration 테스트
43. Backend: windows multi sentiment 응답 형식 테스트
44. Backend: ETag 304 경로 테스트
45. Backend: tokens filter + sentiment_range 필터 테스트
46. Backend: rate limit rule 테스트 (delta/full 구분)
47. Frontend: mergeNewArticles 중복/정렬 테스트
48. Frontend: combined regex highlight 성능 벤치(mock) 
49. Frontend: delta race 방지 테스트 (mock fetch 지연)
50. Frontend: localStorage persistence e2e (Cypress 등) 

## 9. Prioritization Summary
P0: 1,2,3,17,18,19,23,24,42,47  
P1: 4,5,7,8,20,21,22,25,43,44,48  
P2: 6,9,10,26,27,28,29,30,31,45,49  
P3: 11,12,13,14,15,16,32,33,34,35,36,37,38,39,40,41,46,50  

(초안 v1)

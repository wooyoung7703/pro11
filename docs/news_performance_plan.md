# 뉴스 모듈 성능 최적화 제안

## 1. 문제 요약
현재 `NewsView.vue` 에서 다음 비용이 중첩되어 렌더/계산 과다 발생 가능성이 있습니다:
- 전체 기사 배열에 대해 (검색 토큰 → 토큰 비활성 목록 → 날짜/심볼 필터 → 정렬 → 하이라이트) 순서로 매 렌더 시 재계산
- polling 주기마다 동일 필터 체인 재실행
- 본문(body) lazy fetch 시 UI state 반영이 비효율적 (개별 ref 다수)
- 하이라이트: 토큰 분리 후 정규식 다중 생성 (대소문/부분일치)로 O(N*M) 문자열 스캔

## 2. 목표 KPI
| 항목 | 현재(추정) | 목표 |
|------|-----------|------|
| 평균 렌더 당 필터/정렬 수행 횟수 | 1회 이상(reactive 체인) | 1회 캐싱 (메모) |
| 하이라이트 처리 시간 (1000 기사, 3 토큰) | >120ms 추정 | <30ms |
| 불필요한 재계산 발생률 | 다수 | 50% 이상 제거 |
| 초기 payload 크기 | 전체 본문 포함 가능 | 30~60% 축소 (summary만) |

## 3. 최적화 전략 개요
1. 계산 파이프라인 단계별 메모화 + 의존 그래프 분리
2. 서버 사이드 delta fetch & pagination 도입 이후 클라이언트 merge minimal diff
3. 하이라이트 토큰 컴파일: 하나의 combined RegExp (|) 로 통합 후 스트리밍 처리
4. Virt 리스트(virtual scroll) 도입 (예: Vue Virtual Scroller) 로 DOM 노드 상한 제한
5. Lazy body fetch batching: 펼친 기사 id 수집하여 debounce 후 concat 요청
6. Suspense + skeleton 사용으로 main thread blocking 체감 감소
7. 정렬을 필터 후 한 번만 수행; stable sort + key 함축
8. Reactive cost 절감: derived 데이터는 shallowRef + manual trigger

## 4. 세부 항목
### 4.1 파이프라인 분리 & 메모화
- 원본 articles → enabledTokens 계산 → tokenFiltered → date/symbol filtered → sorted → highlighted
- 각 단계 output + input signature(hash) 저장 (articles.version, disabledTokens.join(','), searchTokens.join('|'), filterRange tuple 등)
- 변경 없는 단계는 이전 결과 반환 → 평균 체인 수행 횟수 감소

### 4.2 Delta Fetch + Merge
- since_ts 파라미터 적용 후 신규 기사 prepend
- 이미 존재하는 id set 이용 O(1) 중복 skip
- window 유지: 최대 N(예: 500) 초과분 truncate tail

### 4.3 하이라이트 엔진 개선
- 토큰 전처리: 소문자화, 특수문자 escape
- Combined regex: new RegExp(`(token1|token2|token3)`, 'gi')
- 매칭 시 slice index 기반 빌더로 span 조립 → 다중 replace 회수 감소
- 1000개 기사 기준 사전 컴파일 1회; 기사 본문/제목에 재사용

### 4.4 Virtual Scroll
- 1000+ DOM row 렌더 방지; 화면 내 30~40개만 유지
- Expand된 본문 height 추정 위해 placeholder height 전략

### 4.5 Lazy Body Fetch Batching
- unfold 이벤트마다 queue push; 150ms debounce 후 `/api/news/body?ids=...` bulk
- 응답 merge map[id].body 채움; 실패 id retry backoff

### 4.6 Suspense / Skeleton
- Delta fetch 직후 상단 새로운 n개 placeholder skeleton 표시 → 사용자 체감 latency 감소

### 4.7 정렬 최적화
- Comparator 간접 참조 제거 (branchless timestamp desc)
- 정렬 키 캐싱: 기사 load 시 numericTimestamp 필드 추가

### 4.8 Reactive 최소화
- Large array immutable replace 대신 diff patch (push/unshift) + shallowRef 사용
- highlight 결과는 비동기 워커로 오프로드 고려 (추가 단계)

## 5. 구현 우선순위 (빠른 가성비)
1) Delta fetch + summary payload (백엔드 연계)  
2) 파이프라인 메모화  
3) Combined highlight regex  
4) Lazy body batch fetch  
5) Virtual scroll  
6) Reactive diff + worker offload (선택)  

## 6. 리스크 & 대응
| 리스크 | 영향 | 대응 |
|--------|------|------|
| Delta fetch 순서 역전 | 잘못된 정렬 | 서버 응답 timestamp 기반 재정렬 강제 |
| 메모화 stale | 오래된 UI | signature 광범위 포함 + fallback full 재계산 |
| Virtual scroll 측정 오류 | 잘림/빈 공간 | 라이브러리 기본 measurement + resize observer |
| Combined regex ReDoS | 성능 저하 | 토큰 길이 제한 & escape | 
| Batch fetch 실패 | 본문 지연 | 개별 fallback 단건 요청 허용 |

## 7. 추가 측정 포인트
- metric: news_frontend_highlight_ms (perf API) → histogram bucket
- metric: news_frontend_pipeline_recalc_total (counter, label=stage)
- metric: news_body_batch_size (distribution)

## 8. 완료 정의 (DoD)
- 1000개 mock articles 기준 파이프라인 재계산 횟수 50% 이상 감소
- 하이라이트 처리 30ms 이내 (Chrome i7 기준) 벤치
- 초기 fetch payload 크기 baseline 대비 ≥30% 감소 (summary 적용)

## 9. 후속 문서 링크
- UX / Backend 확장 / Observability / Security 문서에서 해당 전략 교차참조 예정

(초안 v1)

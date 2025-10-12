# 뉴스 모듈 보안 / 신뢰성 개선 제안

## 1. 위협 / 취약 가능성 요약
| 영역 | 위험 | 설명 |
|------|------|------|
| XSS | 본문/제목 HTML 인젝션 | 외부 뉴스 소스가 script/iframe 주입 가능성 |
| Token 기반 필터 | Regex DoS | 과도하게 긴 토큰/특수 패턴 입력 |
| 대량 Polling | Rate Abuse | 짧은 주기로 무한 호출 |
| Race Condition | Delta merge 중복 | 동시 fetch 완료 순서 뒤섞임 |
| Fetch 실패 | 무한 재시도 | 네트워크 불안정시 백엔드 압박 |
| LocalStorage | 변조 | disabledTokens 조작으로 비정상 상태 |

## 2. 보안 강화 항목
### 2.1 출력 Sanitization
- 서버: strip HTML (bleach 등) + 허용 태그 화이트리스트 (기본: none)
- 프론트: v-html 사용 금지, plain text binding

### 2.2 입력 Validation
- tokens: 길이 1~32, 알파뉴메릭+(-_.) 허용, 최대 20개
- windows: 최대 5개, 범위 제한
- sentiment range: sane bound [-1,1]

### 2.3 Rate Limiting
- /api/news/recent per api_key: 30 req / 60s (delta 모드), 10 req / 60s (full fetch)
- 429 응답 + Retry-After 헤더

### 2.4 Abuse 감지
- 동일 IP 에서 high error ratio (>=0.5) 5분 지속 → temp block (in-memory TTL)

### 2.5 Regex / Highlight Safe Guard
- Combined regex 길이 한도 (총 패턴 512 chars 이하)
- Catastrophic backtracking 방지 위해 단순 alternation + escape

## 3. 신뢰성 (Resilience) 항목
### 3.1 Fetch Backoff
- 지수 backoff: base 1.5, max 60s, jitter 0~250ms
- 에러 유형 분류: network vs 4xx vs 5xx (4xx 즉시 중단)

### 3.2 Delta Consistency
- in-flight map: endpoint+since_ts 키; 새 요청 이전 요청 abort
- merge 시 id set 검사 및 timestamp ordering 재검증

### 3.3 Batch Body Fallback
- batch 실패 시 개별 최대 3개 재시도; 이후 경고 로그만 남기고 skip

### 3.4 LocalStorage 무결성
- JSON parse 실패 또는 스키마 불일치 → 초기화 + metric(news_frontend_state_reset_total)
- 서명(signature) 필드(optional) 로 데이터 버전 검증

### 3.5 Graceful Degradation
- sentiment API 실패 시 UI: 회색 배지 + tooltip("sentiment 잠시 사용 불가")

## 4. 메트릭 / 로깅
| 항목 | 메트릭 |
|------|--------|
| Rate limit 차단 | news_recent_rate_limited_total |
| Backoff 시퀀스 | news_frontend_backoff_applied_total |
| Delta merge conflict | news_frontend_delta_conflict_total |
| State reset | news_frontend_state_reset_total |
| Sanitization 적용 count | news_sanitized_articles_total |

## 5. 우선순위
1) Sanitization + v-html 제거  
2) 입력 Validation 강화  
3) Rate limiting (news 전용 rule)  
4) Fetch Backoff + abort controller  
5) LocalStorage 무결성 체크  
6) Abuse 감지 / IP block  

## 6. DoD
- 악의적 script 태그 테스트 시 렌더링/실행 차단 확인
- 60초 50회 full fetch 시 429 반환
- Delta fetch race 테스트 (병렬 5개) 후 중복/역정렬 0건

(초안 v1)

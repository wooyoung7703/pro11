# 뉴스 백엔드 확장 제안 (/api/news/recent 등)

## 1. 요구사항 요약
프론트 최적화(Delta fetch, Infinite scroll, Summary payload, Multi-window sentiment)를 지원하기 위한 API 파라미터 및 응답 구조 확장 필요.

## 2. 신규/확장 파라미터 설계
| 파라미터 | 형식 | 설명 | 예시 |
|----------|------|------|------|
| since_ts | int (epoch ms) | 이 시각(포함 이후) 신규 기사만 반환 (delta fetch) | since_ts=1733145600000 |
| cursor | 문자열(opaque) | 페이지네이션 위치 식별자 (timestamp 기반 encode) | cursor=eyJ0cyI6MTczMzE0NTYwMDAwfQ== |
| limit | int | 페이지 크기 (기본 50, 최대 200) | limit=100 |
| summary_only | bool | 본문 전문 제외, 요약/메타만 | summary_only=true |
| windows | csv(int) | sentiment rolling window 목록 | windows=5,15,30 |
| symbols | csv(string) | 특정 심볼 필터 (백엔드 수준) | symbols=BTCUSDT,ETHUSDT |
| tokens | csv(string) | 서버 측 토큰 포함 필터 (optional) | tokens=binance,sec |
| sentiment_min | float | 최소 sentiment 임계 | sentiment_min=0.2 |
| sentiment_max | float | 최대 sentiment 임계 | sentiment_max=0.8 |
| etag | header | 조건부 응답용 If-None-Match | ETag: "news-snap-<hash>" |

## 3. 응답 구조 예시
```
{
  "items": [
    {
      "id": "uuid",
      "symbol": "BTCUSDT",
      "title": "...",
      "summary": "요약 텍스트",
      "sentiment": {"w5": 0.34, "w15":0.41, "w30":0.37},
      "published_ts": 1733145600123,
      "source": "coindesk"
    }
  ],
  "next_cursor": "eyJ0cyI6MTczMzE0NTYwMDEyfQ==",
  "delta": true,
  "etag": "news-snap-a1b2c3",
  "server_filtered": {
     "symbols": ["BTCUSDT"],
     "windows": [5,15,30],
     "tokens": ["binance"],
     "sentiment_range": [0.2, 0.8]
  }
}
```

## 4. 내부 처리 플로우
1. 입력 파라미터 validate (limit, windows set size, range) 
2. since_ts 우선 → delta mode; 없으면 cursor 기반 페이지
3. DB 쿼리 빌드: 조건(where) + ORDER BY published_ts DESC LIMIT
4. sentiment multi-window 계산 (cache 활용) → dict merge
5. summary_only=true 시 body 컬럼 제외 (SELECT 절 최소화)
6. next_cursor = encode(last_row_ts)
7. 응답 hash (ids + last_ts + filter signature) → ETag
8. If-None-Match 와 동일하면 304 + 빈 body

## 5. 캐시 전략
- In-memory LRU(key: query signature) → rows + etag + generated_ts
- TTL 예: 5s (burst 방어) 
- Prometheus: news_recent_cache_hit_total, miss_total, etag_not_modified_total

## 6. Validation 규칙
| 항목 | 규칙 |
|------|------|
| limit | 1 ≤ limit ≤ 200 |
| windows | 개수 ≤ 5, 값 1 ≤ w ≤ 360 (분 단위 예시) |
| tokens 길이 | 각 1~32, 총 문자열 256 이하 |
| sentiment range | -1.0 ≤ min < max ≤ 1.0 |

## 7. 에러 코드
| 코드 | 상황 |
|------|------|
| 400 | 파라미터 검증 실패 |
| 416 | limit 초과 |
| 304 | ETag 불일치(조건부 응답) |

## 8. 메트릭
- news_recent_requests_total{delta=bool, summary_only=bool}
- news_recent_rows_returned_sum
- news_recent_etag_reused_total
- news_recent_latency_seconds (histogram)
- news_recent_cache_hit_total / miss_total

## 9. 구현 순서
1) since_ts + summary_only + 기본 응답 리팩터  
2) windows 다중 지원 (sentiment dict)  
3) cursor 페이지네이션  
4) ETag + 캐시 계층  
5) 서버측 tokens/sentiment 필터  

## 10. DoD
- since_ts 호출 시 기존 전체 fetch 대비 payload ≥60% 감소 (샘플) 
- ETag 사용 시 304 비율 20% 이상 관찰 (캐시 warm 상황) 
- 메트릭 대시보드에서 latency p95 추적 가능

(초안 v1)

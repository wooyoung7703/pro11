# News API Endpoints Reference

본 문서는 뉴스 수집/분석 관련 최신 엔드포인트 사양, 파라미터, 응답 구조, 운영 팁을 요약합니다.

## Index
- [/api/news/status](#get-apinewsstatus)
- [/api/news/recent](#get-apinewsrecent)
- [/api/news/article/{id}](#get-apinewsarticleid)
- [/api/news/fetch/manual](#post-apinewsfetchmanual)
- [/api/news/backfill](#post-apinewsbackfill)
- [/api/news/sources](#get-apinewssources)
- [/api/news/summary](#get-apinewssummary)
- [/api/news/search](#get-apinewssearch)

---

## GET /api/news/status
수집 루프 실행 상태 & 지연(lag) 지표.

응답 필드:
| 필드 | 설명 |
|------|------|
| running | 루프 활성 여부 |
| fetch_runs | 누적 fetch 실행 수 |
| fetch_errors | 누적 에러 수 |
| articles_ingested | 누적 유니크 기사 수 |
| lag_seconds | 최신 unique 기사와 현재 시각 차이(초) |
| last_run_ts | 마지막 fetch 실행 epoch(초) |
| last_ingested_ts | 마지막 unique 기사 epoch(초) |
| poll_interval | 폴링 간격(초) |

운영 팁: `lag_seconds` 상승 + `fetch_runs` 증가 O → feed 측 신규 기사 부족 가능성. 둘 다 증가 X → 루프 중단/예외 로그 확인.

---
## GET /api/news/recent
증분(delta) 또는 초기 기사 목록 조회.

쿼리 파라미터:
| 파라미터 | 타입 | 기본 | 설명 |
|----------|------|------|------|
| limit | int | 100 | 최대 반환 기사 수 |
| since_ts | int | (없음) | 지정 시 해당 epoch 초 이후 신규 기사만(delta) 시도 |
| summary_only | int/bool | 1 | 1이면 body 미포함 (lazy load) |

응답:
```
{
  "items": [ {id,title,summary,source,published_ts}, ... ],
  "latest_ts": 1696234000,
  "delta": true
}
```

---
## GET /api/news/article/{id}
본문(body) 지연 로딩.

응답: `{ "article": { id, title, body, ... } }`

---
## POST /api/news/fetch/manual
즉시 한 번 fetch 실행. (백엔드 루프 interval 대기 스킵)

성공 시 204 또는 `{status:"ok"}` (구현에 따라).

---
## POST /api/news/backfill
과거 N일 백필.

쿼리 파라미터:
| 파라미터 | 타입 | 설명 |
|----------|------|------|
| days | int | 최근 N일 (feed 제공 한계 내) |

TIP: 무리한 큰 days 값은 feed 자체 한계 또는 rate limit 유발. 점증(2→5→10) 형태 권장.

---
## GET /api/news/sources
소스별 헬스 / dedup / stall / anomaly.

쿼리 파라미터:
| 파라미터 | 타입 | 기본 | 설명 |
|----------|------|------|------|
| include_history | int/bool | 0 | 1이면 간단한 dedup 비율 history 포함 |

응답 예시:
```
{
  "sources": [
    {"source":"coindesk","score":0.83,"stalled":false,"dedup_anomaly":false,
     "backoff_remaining":0,"last_unique_ts":1696234000,"dedup_last_ratio":0.42,
     "dedup_history":[0.35,0.40,0.42]}
  ],
  "dedup_history": {"coindesk":[{"ts":1696234000,"raw":12,"kept":5,"ratio":0.41}]} // (선택)
}
```

Score 개략: 1.0 기준, stall/anomaly/backoff 시 가중 감점 (세부 로직 향후 문서화)

---
## GET /api/news/summary
시간 윈도우 기반 집계.

쿼리 파라미터:
| 파라미터 | 타입 | 기본 | 설명 |
|----------|------|------|------|
| window_minutes | int | 1440 | 집계할 lookback 분 |

응답 예시:
```
{
  "window_minutes":120,
  "total":340,
  "sources":[{"source":"coindesk","count":90,"ratio":0.265}],
  "sentiment":{"avg":0.12,"std":0.34,"pos":120,"neg":45,"neutral":175}
}
```

---
## GET /api/news/search
기사 검색 (제목/요약/본문).

쿼리 파라미터:
| 파라미터 | 타입 | 기본 | 설명 |
|----------|------|------|------|
| q | string | (필수) | 2글자 이상 키워드 (ILIKE) |
| days | int | 7 | 검색 기간(최근 N일) |
| limit | int | 200 | 최대 결과 수 (정책) |
| offset | int | 0 | 페이지네이션 시작 |

응답:
```
{ "articles": [ {id,title,summary,source,published_ts,sentiment?}, ... ] }
```

운영 팁:
- 장기간 특정 키워드 absence → feed 필터링 또는 공급자 편향 가능성
- 검색 중 UI auto delta fetch 중단 (검색 결과 덮어쓰기 방지)

---
## 운영 모니터링 체크 (요약)
| 목표 | 엔드포인트 | 기준 |
|------|------------|------|
| 소스 stall 감지 | /api/news/sources | stalled=true 발생 소스 수 |
| dedup 품질 | /api/news/sources | dedup_last_ratio < NEWS_DEDUP_MIN_RATIO 지속 |
| sentiment 편향 | /api/news/summary | avg 급격 이동 / pos/neg 불균형 |
| 실시간 기사 유입 | /api/news/status | lag_seconds 증가 추세 여부 |

---
## FAQ
**Q: dedup_history 와 sources[].dedup_history 차이?**
- 전자는 raw/kept/ratio 구조화된 상세 객체 맵, 후자는 간단 ratio 배열(가벼운 sparkline 용).

**Q: backfill 후 summary 반영 안 됨**
- 백필은 비동기 처리. 약간의 지연 후 `/api/news/summary` 재호출. 여전히 부족하면 feed 원천 제한.

**Q: sentiment null?**
- 분석 파이프라인 아직 본문/요약 전처리 지연 또는 해당 기사 분석 실패. 재시도 로직(향후) 예정.

---
## 향후 로드맵 (요약)
- dedup anomaly 타입 세분화 (volatility vs sustained drop)
- 자동 source disable/reenable 정책 메트릭
- 검색 결과 facet (sentiment / source) 필터
- SSE/WebSocket 기반 실시간 push

---
라이선스/저작권: 사내 전용 (외부 공개 금지)

# Trading Signals & Timeline API

본 문서는 트레이딩 시그널의 저장 및 조회에 관한 엔드포인트와 데이터 모델을 요약합니다.

## 저장되는 데이터

- trading_signals (DB):
  - 시그널 트리거 시 1건 생성(`status=triggered`, `params`, `price`, `extra.metrics`)
  - 주문 제출/거부/체결 시 동일 row에 `executed_at`, `status`, `order_side/size/price`, `error`, `extra.order_response` 업데이트

- autopilot_event_log (DB):
  - 오토파일럿 활성/해제 이벤트가 이벤트 로그로 적재 (`type='signal'|'signal_clear'`, `payload.signal`) 
  - 상태 스냅샷은 `autopilot_state_snapshot`에 주기 저장 (별도 엔드포인트 참고)

두 테이블을 합쳐서 “시그널 타임라인”을 제공합니다.

## Endpoints

### GET /api/trading/signals/recent
최근 시그널 raw 레코드 목록.

쿼리:
- `limit` (기본 100, 최대 500)
- `signal_type` (예: `low_buy`)

응답:
```json
{
  "status": "ok",
  "signals": [
    {"id": 1, "signal_type": "low_buy", "status":"triggered", "price": 0.51,
     "params": {"lookback": 25}, "extra": {"metrics": {...}},
     "order_side": null, "order_size": null, "order_price": null,
     "error": null, "created_ts": 169..., "executed_ts": null}
  ]
}
```

### GET /api/trading/signals/timeline
시그널 생성/실행 + 오토파일럿 이벤트를 시간순으로 합친 뷰.

쿼리:
- `limit` (기본 200, 최대 1000)
- `from_ts`, `to_ts` (epoch seconds)
- `signal_type` (예: `low_buy`)

응답:
```json
{
  "status": "ok",
  "timeline": [
    {"ts": 169..., "source":"trading", "event":"created", "signal_type":"low_buy",
     "symbol": null, "details": {"id":5, "status":"triggered", "params":{...}, "extra":{...}}},
    {"ts": 169..., "source":"autopilot", "event":"signal", "signal_type":"low_buy",
     "symbol":"XRPUSDT", "details": {"signal":{...}, "reason":"..."}},
    {"ts": 169..., "source":"trading", "event":"filled", "signal_type":"low_buy",
     "symbol": null, "details": {"id":5, "order_side":"buy", "order_size":1, "order_price":0.52, "extra":{...}}}
  ]
}
```

비고:
- `source`는 `trading`(trading_signals) 또는 `autopilot`(autopilot_event_log)입니다.
- `event`는 `created`/`filled`/`rejected`/`submitted` 등 트레이딩 상태와 `signal`/`signal_clear`(오토파일럿) 등이 포함됩니다.

### DELETE /api/trading/signals
저장된 `trading_signals`를 비웁니다(디버깅/리셋 용).

쿼리:
- `signal_type` (옵션)

응답: `{ "status":"ok", "deleted": <count>, "signal_type": "low_buy" }`

## 참고
- 시그널은 DB가 단일 진실 소스로 적재되며, 프론트/뷰어에서 `timeline` 엔드포인트로 손쉽게 타임라인을 표시할 수 있습니다.
- 오토파일럿 이벤트 로그는 향후 다른 시그널 소스(ML 등)에도 동일하게 축적됩니다.

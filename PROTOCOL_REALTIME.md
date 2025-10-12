# Realtime Protocol (SSE)

## Overview
- Transport: Server-Sent Events (SSE)
- Endpoint: `/stream/signals`
- Model: Snapshot + Delta
- Ordering: `seq` strictly increases per `epoch`. On reconnect, server may bump `epoch`.
- Heartbeat: `type: "heartbeat"` every 10–20s with `server_time`.

## Event envelope
```json
{
  "type": "snapshot|delta|heartbeat|error",
  "seq": 12345,
  "epoch": "2025-10-08T12:00:00Z",
  "server_time": "2025-10-08T12:00:01Z",
  "channel": "signals",
  "data": { /* channel payload */ }
}
```

## Signals payload (example)
- snapshot `data` contains full state needed by UI (donut mode, probabilities, gates, orders summary)
- delta `data` contains only changed fields

```json
// snapshot
{
  "mode": "buy|price_gate|exit",
  "prob": { "up": 0.61 },
  "gates": {
    "entry_price": 0.5225,
    "anchor_price": 0.5170,
    "exit_threshold": 0.65,
    "exit_remain": 0.09
  },
  "position": {
    "in_position": true,
    "avg_entry": 0.5201,
    "scalein_legs": 1
  }
}
```

## Client rules
- Maintain `lastSeq` per `epoch`.
- Drop events with `seq <= lastSeq`.
- If `seq` gap detected, request resync (HTTP snapshot fetch) and continue.
- Use `server_time` for age/cooldown display.

## Reconnect
- Exponential backoff (e.g., 1s, 2s, 4s, 8s, max 30s)
- On reconnect, accept new `epoch` and reset `lastSeq`.

#!/usr/bin/env bash
set -euo pipefail
API_KEY=${API_KEY:-dev-key}
BASE=${BASE_URL:-http://localhost:8000}
SYM=${1:-XRPUSDT}
ITV=${2:-1m}
POLL_MAX=${POLL_MAX:-30}
SLEEP=${SLEEP:-2}

hdr() { echo; echo "==== $* ===="; }
json_get() { local key=$1; python - <<PY
import sys,json
try:
  data=json.load(sys.stdin)
  v=data.get("$key")
  if isinstance(v,float):
    print(f"{v:.6f}")
  else:
    print(v)
except Exception:
  pass
PY
}

hdr "START YEAR BACKFILL $SYM $ITV";
START=$(curl -s -H X-API-Key:$API_KEY -X POST "$BASE/api/ohlcv/backfill/year?symbol=$SYM&interval=$ITV")
echo "$START"
STATUS=$(echo "$START" | json_get status || true)

hdr "POLL STATUS";
for i in $(seq 1 $POLL_MAX); do
  sleep $SLEEP
  RESP=$(curl -s -H X-API-Key:$API_KEY "$BASE/api/ohlcv/backfill/year/status?symbol=$SYM&interval=$ITV")
  STATE=$(echo "$RESP" | json_get status || true)
  LOADED=$(echo "$RESP" | json_get loaded_bars || true)
  EXPECT=$(echo "$RESP" | json_get expected_bars || true)
  PCT=$(echo "$RESP" | json_get percent || true)
  REQS=$(echo "$RESP" | json_get request_count || true)
  echo "[$(date +%H:%M:%S)] iter=$i state=$STATE loaded=$LOADED/$EXPECT pct=$PCT reqs=$REQS"
  if [[ "$STATE" != "running" && "$STATE" != "pending" ]]; then
    FINAL_RESP=$RESP
    break
  fi
done

if [[ -z "${FINAL_RESP:-}" ]]; then
  echo "Timed out without completion" >&2
  FINAL_RESP=$RESP
fi
hdr "FINAL STATE RAW"; echo "$FINAL_RESP"

hdr "META"; META=$(curl -s -H X-API-Key:$API_KEY "$BASE/api/ohlcv/meta?symbol=$SYM&interval=$ITV"); echo "$META"
COMP365=$(echo "$META" | json_get completeness365d || true)
COMP=$(echo "$META" | json_get completeness || true)
echo "Parsed completeness365d=$COMP365 overall=$COMP"

hdr "RECENT 3 CANDLES"; curl -s -H X-API-Key:$API_KEY "$BASE/api/ohlcv/recent?symbol=$SYM&interval=$ITV&limit=3" | python - <<PY
import sys,json
try:
  data=json.load(sys.stdin)
  if isinstance(data,list):
    for row in data:
      print(row.get("open_time"), row.get("open"), row.get("close"))
except Exception as e:
  print("parse_error",e,file=sys.stderr)
PY

hdr "VERIFY SCRIPT (SAMPLES)"; SYMBOL=$SYM INTERVAL=$ITV python scripts/verify_year_backfill.py | sed 's/^/verify: /'

hdr "SUMMARY";
echo "Symbol=$SYM Interval=$ITV FinalState=$(echo \"$FINAL_RESP\" | json_get status) completeness365d=$COMP365 overall=$COMP"

#!/usr/bin/env bash
# Poll the dev service readiness & summarize. Designed for FAST_STARTUP + reload workflows.
# Usage: ./scripts/dev-health.sh [host:port] [max_attempts] [sleep_seconds]
# Defaults: host:port=localhost:8000, max_attempts=30, sleep=1

TARGET=${1:-localhost:8000}
MAX=${2:-30}
SLEEP=${3:-1}
URL=http://$TARGET/health/ready

printf "Checking readiness at %s (max %s attempts, interval %ss)\n" "$URL" "$MAX" "$SLEEP"

for i in $(seq 1 $MAX); do
  RESP=$(curl -s -m 2 "$URL" || true)
  if echo "$RESP" | grep -q '"status"'; then
    STATUS=$(echo "$RESP" | sed -n 's/.*"status"[ ]*:[ ]*"\([^"]*\)".*/\1/p' | head -n1)
    if [ "$STATUS" = "ready" ]; then
      echo "READY after ${i} attempts"
      echo "$RESP" | jq . 2>/dev/null || echo "$RESP"
      exit 0
    fi
    echo "Attempt $i: not ready -> $STATUS"
  else
    echo "Attempt $i: no valid JSON (service starting?)"
  fi
  sleep $SLEEP
 done

echo "Timed out waiting for readiness" >&2
curl -s "$URL" || true
exit 1

import os
import time
import json
import urllib.request
import urllib.error


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return resp.getcode(), body
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            body = str(e)
        return e.code, body
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def run_once():
    backend_url = _env("BACKEND_URL", "http://app:8000")
    api_key = _env("RETENTION_API_KEY", _env("API_KEY", ""))
    older_than_days = int(_env("RETENTION_OLDER_THAN_DAYS", "30"))
    max_rows = int(_env("RETENTION_MAX_ROWS", "5000"))
    include_tables = _env("RETENTION_TABLES", "trading_signals,autopilot_event_log").split(",")

    url = backend_url.rstrip("/") + "/admin/trading/purge"
    payload = {
        "older_than_days": older_than_days,
        "max_rows": max_rows,
        "tables": [t.strip() for t in include_tables if t.strip()],
    }
    headers = {"X-API-Key": api_key} if api_key else {}

    code, body = post_json(url, payload, headers)
    print(f"[retention] POST {url} -> {code} body={body[:200]}")
    return code


def main():
    enabled = _env("RETENTION_ENABLED", "1") in ("1", "true", "True")
    interval = int(_env("RETENTION_INTERVAL_SECONDS", str(7 * 24 * 3600)))
    if not enabled:
        print("[retention] Disabled via RETENTION_ENABLED=0")
        return

    # First run shortly after start to keep dev feedback tight
    initial_delay = int(_env("RETENTION_INITIAL_DELAY_SECONDS", "15"))
    print(
        f"[retention] Starting. interval={interval}s older_than_days={_env('RETENTION_OLDER_THAN_DAYS','30')} max_rows={_env('RETENTION_MAX_ROWS','5000')}"
    )
    time.sleep(max(0, initial_delay))
    while True:
        try:
            run_once()
        except Exception as e:  # noqa: BLE001
            print(f"[retention] run error: {e}")
        time.sleep(max(60, interval))


if __name__ == "__main__":
    main()

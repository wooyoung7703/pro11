"""Standalone RSS fetch diagnostic.

Usage (host):
  poetry run python scripts/debug_rss.py --url https://rss.cnn.com/rss/edition_world.rss
  poetry run python scripts/debug_rss.py --env NEWS_RSS_FEEDS

Inside container:
  docker compose exec app poetry run python scripts/debug_rss.py --env NEWS_RSS_FEEDS

Reports:
- DNS / connection time
- HTTP status (if feedparser exposes)
- Entry count & sample titles
- Published timestamp parse success ratio
- Earliest/Latest published time (delta minutes)
- Duplicate hash rate (local computation)

Exit codes:
 0 success, 2 no entries, 3 network/parse error
"""
from __future__ import annotations
import argparse, time, hashlib, sys, os
from typing import List

try:
    import feedparser  # type: ignore
except Exception as e:  # noqa: BLE001
    print(f"[FATAL] feedparser import failed: {e}")
    sys.exit(3)


def fetch(url: str):
    t0 = time.perf_counter()
    feed = feedparser.parse(url)
    elapsed = time.perf_counter() - t0
    entries = getattr(feed, 'entries', []) or []
    status = getattr(feed, 'status', None)
    bozo = getattr(feed, 'bozo', None)
    bozo_exc = getattr(feed, 'bozo_exception', None)
    return {
        "url": url,
        "status": status,
        "time_sec": round(elapsed, 3),
        "bozo": bozo,
        "bozo_exception": repr(bozo_exc)[:200] if bozo_exc else None,
        "entries": entries,
    }


def analyze(entries) -> dict:
    if not entries:
        return {"count": 0}
    hashes = []
    titles: List[str] = []
    published = []
    for e in entries:
        title = getattr(e, 'title', None) or getattr(e, 'id', None) or ''
        link = getattr(e, 'link', None) or ''
        ts_val = None
        st = getattr(e, 'published_parsed', None) or getattr(e, 'updated_parsed', None)
        if st:
            try:
                ts_val = int(time.mktime(st))
            except Exception:
                ts_val = None
        if ts_val:
            published.append(ts_val)
        base = f"{link}|{ts_val or 0}|{title}".encode()
        h = hashlib.sha256(base).hexdigest()[:24]
        hashes.append(h)
        if title:
            titles.append(title[:100])
    dup_rate = 0.0
    if hashes:
        uniq = len(set(hashes))
        dup_rate = 1 - (uniq / len(hashes))
    span_min = None
    span_delta_min = None
    if published:
        span_min = (min(published), max(published))
        span_delta_min = round((max(published)-min(published))/60,2)
    return {
        "count": len(entries),
        "duplicate_rate": round(dup_rate, 3),
        "with_published_ts": sum(1 for _ in published),
        "time_span_min": span_min,
        "span_delta_minutes": span_delta_min,
        "sample_titles": titles[:5],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', help='Single RSS feed URL to test')
    ap.add_argument('--env', help='Env var containing comma separated URLs (e.g. NEWS_RSS_FEEDS)')
    args = ap.parse_args()

    urls: List[str] = []
    if args.url:
        urls.append(args.url.strip())
    if args.env:
        raw = os.getenv(args.env, '').strip()
        if raw:
            urls.extend([u.strip() for u in raw.split(',') if u.strip()])
    if not urls:
        print('[ERROR] No URLs provided (use --url or --env).')
        sys.exit(2)

    overall_rc = 0
    for u in urls:
        print(f"=== Fetch: {u}")
        try:
            res = fetch(u)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] fetch error: {e}")
            overall_rc = 3
            continue
        print(f" status={res['status']} time={res['time_sec']}s bozo={res['bozo']} bozo_exc={res['bozo_exception']}")
        info = analyze(res['entries'])
        if info['count'] == 0:
            print(' entries=0 (EMPTY)')
            overall_rc = max(overall_rc, 2)
        else:
            print(f" entries={info['count']} dup_rate={info['duplicate_rate']} span={info['span_delta_minutes']}m titles={info['sample_titles']}")
    sys.exit(overall_rc)

if __name__ == '__main__':  # pragma: no cover
    main()

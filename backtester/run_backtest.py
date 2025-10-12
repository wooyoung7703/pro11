import json
import os
import sys
import pandas as pd
import argparse

# Ensure local package is importable without install
sys.path.insert(0, os.path.dirname(__file__))
from sbtester.engine import Engine, Params, Candle, Fill

parser = argparse.ArgumentParser()
parser.add_argument('--csv', default=None)
parser.add_argument('--csv-url', default=None, help='HTTP endpoint that returns OHLCV JSON list with fields timestamp,open,high,low,close,volume')
parser.add_argument('--events', default=None)
parser.add_argument('--bundles', default=None)
parser.add_argument('--trades', default=None)
parser.add_argument('--from-ts', type=int, default=None, help='inclusive start timestamp to filter list outputs')
parser.add_argument('--to-ts', type=int, default=None, help='inclusive end timestamp to filter list outputs')
parser.add_argument('--fills', default=None, help='CSV of real fills with columns: ts,side,price,qty,fee')
parser.add_argument('--fills-url', default=None, help='HTTP endpoint that returns fills JSON list with fields ts,side,price,qty,fee')
parser.add_argument('--auth-bearer', default=None, help='Bearer token for HTTP Authorization header')
parser.add_argument('--http-header', action='append', default=None, help='Extra HTTP header(s), repeatable. Format: Key: Value')
parser.add_argument('--max-pages', type=int, default=1, help='If URL contains {page}, iterate pages up to this count until empty array returned')
parser.add_argument('--show', default='trades', choices=['trades','events','bundles','none'], help='Which list to print to stdout (CSV). Default trades')
parser.add_argument('--limit', type=int, default=50, help='Max rows to print for --show list (default 50)')
parser.add_argument('--sort', default='time', choices=['time','none'], help='Sort strategy for --show list (time or none). Default time')
parser.add_argument('--stdout-format', default='csv', choices=['csv','json','table'], help='Format for stdout list output. Default csv')
args = parser.parse_args()

# Validate required input pairs
if not args.csv and not args.csv_url:
    raise SystemExit('Provide OHLCV input via --csv or --csv-url')
if not args.fills and not args.fills_url:
    raise SystemExit('Provide fills input via --fills or --fills-url')

import requests

def build_headers():
    hdrs = {}
    if args.auth_bearer:
        hdrs['Authorization'] = f'Bearer {args.auth_bearer}'
    if args.http_header:
        for h in args.http_header:
            if ':' in h:
                k,v = h.split(':', 1)
                hdrs[k.strip()] = v.strip()
    return hdrs

def fetch_paged_json(url: str, need_keys: set) -> list:
    headers = build_headers()
    data_all = []
    if '{page}' in url:
        for i in range(1, max(1, args.max_pages)+1):
            u = url.replace('{page}', str(i))
            resp = requests.get(u, headers=headers, timeout=30)
            resp.raise_for_status()
            part = resp.json()
            if not part:
                break
            if not isinstance(part, list):
                raise SystemExit('HTTP returned non-array JSON')
            for obj in part:
                if not need_keys.issubset(obj.keys()):
                    raise SystemExit(f'HTTP JSON missing fields. Need {need_keys}')
            data_all.extend(part)
    else:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        part = resp.json()
        if not isinstance(part, list):
            raise SystemExit('HTTP returned non-array JSON')
        for obj in part:
            if not need_keys.issubset(obj.keys()):
                raise SystemExit(f'HTTP JSON missing fields. Need {need_keys}')
        data_all = part
    return data_all
if args.csv_url:
    need = {"timestamp","open","high","low","close","volume"}
    data = fetch_paged_json(args.csv_url, need)
    candles = [Candle(int(o['timestamp']), float(o['open']), float(o['high']), float(o['low']), float(o['close']), float(o['volume'])) for o in data]
else:
    df = pd.read_csv(args.csv)
    need = {"timestamp","open","high","low","close","volume"}
    if not need.issubset(df.columns):
        raise SystemExit(f"Missing columns. Need {need}")
    candles = [Candle(int(r.timestamp), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume)) for r in df.itertuples(index=False)]

eng = Engine(Params())

if args.fills_url:
    need_f = {"ts","side","price","qty","fee"}
    data = fetch_paged_json(args.fills_url, need_f)
    fills = [Fill(int(o['ts']), str(o['side']), float(o['price']), float(o['qty']), float(o['fee'])) for o in data]
else:
    fills_df = pd.read_csv(args.fills)
    need_f = {"ts","side","price","qty","fee"}
    if not need_f.issubset(fills_df.columns):
        raise SystemExit(f"Missing columns in fills. Need {need_f}")
    fills = [Fill(int(r.ts), str(r.side), float(r.price), float(r.qty), float(r.fee)) for r in fills_df.itertuples(index=False)]
out = eng.run_replay(candles, fills)

# Optional window filtering for list outputs (events/bundles)
events = out["events"]
bundles = out.get("buy_bundles", [])
trades = out.get("trades", [])
if args.from_ts is not None and args.to_ts is not None:
    f, t = args.from_ts, args.to_ts
    events = [e for e in events if f <= int(e["ts"]) <= t]
    bundles = [b for b in bundles if not (int(b["ts_end"]) < f or int(b["ts_start"]) > t)]

# Timestamp helpers
from datetime import datetime, timezone
def to_ms(ts:int)->int:
    # Heuristic: if ts looks like seconds, convert to ms
    return ts*1000 if ts < 10**11 else ts
def to_iso(ms:int)->str:
    try:
        return datetime.fromtimestamp(ms/1000, tz=timezone.utc).isoformat().replace('+00:00','Z')
    except Exception:
        return ""

# Enrich rows with *_ms and *_iso
def enrich_events(rows):
    out=[]
    for r in rows:
        ms=to_ms(int(r["ts"]))
        r2=dict(r)
        r2["ts_ms"]=ms
        r2["ts_iso"]=to_iso(ms)
        out.append(r2)
    return out
def enrich_bundles(rows):
    out=[]
    for r in rows:
        s=to_ms(int(r["ts_start"]))
        e=to_ms(int(r["ts_end"]))
        r2=dict(r)
        r2["ts_start_ms"]=s
        r2["ts_end_ms"]=e
        r2["ts_start_iso"]=to_iso(s)
        r2["ts_end_iso"]=to_iso(e)
        out.append(r2)
    return out
def enrich_trades(rows):
    out=[]
    for r in rows:
        r2=dict(r)
        ets=r.get("entry_ts")
        xTs=r.get("exit_ts")
        if ets is not None:
            ems=to_ms(int(ets))
            r2["entry_ts_ms"]=ems
            r2["entry_ts_iso"]=to_iso(ems)
        if xTs is not None:
            xms=to_ms(int(xTs))
            r2["exit_ts_ms"]=xms
            r2["exit_ts_iso"]=to_iso(xms)
        out.append(r2)
    return out

# Print full summary (engine scope) and window counters for transparency
summary = out["summary"]
window_counts = {
    "buys": sum(1 for e in events if e["type"] in ("entry","scale_in")),
    "partials": sum(1 for e in events if e["type"] == "partial_exit"),
    "full_exits": sum(1 for e in events if e["type"] == "full_exit"),
}
window_counts["trades"] = window_counts["full_exits"]

print(json.dumps({"summary": summary, "window_counts": window_counts}, ensure_ascii=False, indent=2))

if args.events:
    pd.DataFrame(enrich_events(events)).to_csv(args.events, index=False)
    print(f"events -> {args.events}")
if args.bundles:
    pd.DataFrame(enrich_bundles(bundles)).to_csv(args.bundles, index=False)
    print(f"buy_bundles -> {args.bundles}")
if args.trades:
    pd.DataFrame(enrich_trades(trades)).to_csv(args.trades, index=False)
    print(f"trades -> {args.trades}")

# --- Commonly used stdout list output (CSV/JSON/table) ---
def _select_columns(rows: list[dict], preferred: list[str]) -> list[str]:
    if not rows:
        return []
    cols = []
    for k in preferred:
        if k in rows[0]:
            cols.append(k)
    # Fallback: include any leftover keys not in preferred to avoid losing information
    for k in rows[0].keys():
        if k not in cols:
            cols.append(k)
    return cols

def _sort_rows(rows: list[dict], kind: str) -> list[dict]:
    if kind != 'time' or not rows:
        return rows
    # Try exit_ts, then entry_ts, then ts_start/ts
    def _key(r: dict):
        for c in ('exit_ts','entry_ts','ts_end','ts_start','ts'):
            if c in r and r[c] is not None:
                try:
                    return int(r[c])
                except Exception:
                    pass
        return 0
    return sorted(rows, key=_key)

def _print_stdout(rows: list[dict], fmt: str, columns: list[str], limit: int):
    rows_limited = rows[: max(0, limit)] if limit and limit > 0 else rows
    if fmt == 'json':
        print(json.dumps(rows_limited, ensure_ascii=False))
        return
    import sys as _sys
    import pandas as _pd
    df = _pd.DataFrame(rows_limited)
    if columns:
        # Keep only existing columns in the requested order
        cols = [c for c in columns if c in df.columns]
        if cols:
            df = df[cols]
    if fmt == 'csv':
        df.to_csv(_sys.stdout, index=False)
    else:  # table
        try:
            # Wider display without truncation for console
            _pd.set_option('display.width', 200)
            _pd.set_option('display.max_columns', None)
        except Exception:
            pass
        print(df.to_string(index=False))

if args.show != 'none':
    try:
        if args.show == 'trades':
            rows0 = enrich_trades(trades)
            rows0 = _sort_rows(rows0, args.sort)
            preferred_cols = [
                'entry_ts_iso','exit_ts_iso','side','qty','entry_price','exit_price','gross_pnl','fee','net_pnl','bars','r_multiple'
            ]
            cols = _select_columns(rows0, preferred_cols)
            _print_stdout(rows0, args.stdout_format, cols, args.limit)
        elif args.show == 'events':
            rows0 = enrich_events(events)
            rows0 = _sort_rows(rows0, args.sort)
            preferred_cols = ['ts_iso','type','price','size','note']
            cols = _select_columns(rows0, preferred_cols)
            _print_stdout(rows0, args.stdout_format, cols, args.limit)
        elif args.show == 'bundles':
            rows0 = enrich_bundles(bundles)
            rows0 = _sort_rows(rows0, args.sort)
            preferred_cols = ['ts_start_iso','ts_end_iso','legs','total_size','avg_price']
            cols = _select_columns(rows0, preferred_cols)
            _print_stdout(rows0, args.stdout_format, cols, args.limit)
    except Exception as _e:
        # Non-fatal: keep summary above
        print(f"stdout_list_error: {_e}")

"""Database structural & data presence diagnostic.

Usage:
  poetry run python scripts/diagnose_db.py                # uses env PG vars
  POSTGRES_HOST=... POSTGRES_DB=... poetry run python scripts/diagnose_db.py

What it reports:
  - Connection status & server version
  - For each relevant table pattern (application tables + news):
      * existence (Y/N)
      * column count / missing columns from expected DDL
      * row count (fast estimate for large tables via COUNT(*) fallback)
  - Summary of tables completely empty or structurally missing

Exit codes:
  0 = all required tables exist (even if some empty)
  2 = some tables missing
  3 = connection failure or fatal error
"""
from __future__ import annotations
import os, sys, asyncio, textwrap
from typing import List, Dict

REQUIRED_TABLES = [
    'news_articles',
    'model_inference_log',
    'feature_snapshot',
    'training_jobs',
    'model_registry',
    'model_metrics_history',
    'retrain_events',
    'promotion_events',
    'risk_state',
    'risk_positions',
    'gap_segments',
]

# Minimal expected columns (subset used by code). Not exhaustive.
EXPECTED_COLUMNS: Dict[str, List[str]] = {
    'news_articles': ['id','source','title','published_ts','ingested_ts','hash'],
    'model_inference_log': ['id','created_at','model_id','prediction','realized','resolved_at'],
    'feature_snapshot': ['id','snapshot_ts'],  # adapt if actual schema differs
    'training_jobs': ['id','created_at','status'],
    'model_registry': ['id','created_at','status','version'],
}

async def main() -> int:
    import asyncpg
    host = os.getenv('POSTGRES_HOST', '127.0.0.1')
    port = int(os.getenv('POSTGRES_PORT', '5432'))
    user = os.getenv('POSTGRES_USER', 'postgres')
    pwd = os.getenv('POSTGRES_PASSWORD', '')
    db  = os.getenv('POSTGRES_DB', 'postgres')
    try:
        conn = await asyncpg.connect(host=host, port=port, user=user, password=pwd, database=db, timeout=4.0)
    except Exception as e:  # noqa: BLE001
        print(f"[FATAL] connect_failed: {e}")
        return 3
    try:
        ver = await conn.fetchval('SHOW server_version')
        print(f"Connected: server_version={ver} host={host}:{port} db={db}\n")
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        existing = {r['tablename'] for r in rows}
        missing_tables = [t for t in REQUIRED_TABLES if t not in existing]
        print("Tables present:", ', '.join(sorted(existing)) or '(none)')
        if missing_tables:
            print("[WARN] Missing tables:", ', '.join(missing_tables))
        report = []
        for t in REQUIRED_TABLES:
            if t not in existing:
                report.append({'table': t, 'exists': False, 'cols': 0, 'missing_cols': [], 'row_count': None})
                continue
            cols_rows = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name=$1 ORDER BY ordinal_position", t
            )
            cols = [r['column_name'] for r in cols_rows]
            exp = EXPECTED_COLUMNS.get(t, [])
            missing_cols = [c for c in exp if c not in cols]
            # Row count (COUNT(*)) - acceptable here, expected scale small during diagnostics
            try:
                rc = await conn.fetchval(f'SELECT COUNT(*) FROM {t}')
            except Exception as e2:  # noqa: BLE001
                rc = f"error:{e2}"  # surface error
            report.append({'table': t, 'exists': True, 'cols': len(cols), 'missing_cols': missing_cols, 'row_count': rc})
        # Pretty print
        print("\nDetailed:")
        for r in report:
            status = 'OK' if r['exists'] else 'MISSING'
            print(f"- {r['table']}: {status} cols={r['cols']} row_count={r['row_count']}" + (f" missing_cols={r['missing_cols']}" if r['missing_cols'] else ''))
        missing_structural = [r for r in report if (not r['exists']) or r['missing_cols']]
        if missing_structural:
            print("\n[SUMMARY] Structural issues:")
            for r in missing_structural:
                print(f"  * {r['table']}: exists={r['exists']} missing_cols={r['missing_cols']}")
        empty_tables = [r for r in report if r['exists'] and (r['row_count'] == 0)]
        if empty_tables:
            print("\n[SUMMARY] Empty tables:")
            for r in empty_tables:
                print(f"  * {r['table']} (0 rows)")
        # Exit code logic
        if missing_tables:
            return 2
        return 0
    finally:
        await conn.close()

if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))

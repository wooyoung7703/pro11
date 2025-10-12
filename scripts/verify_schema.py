"""Schema verification script for critical tables/columns used by live calibration & inference logging.

Usage (from project root):
  poetry run python scripts/verify_schema.py

Checks:
- model_inference_log table existence & columns (id, created_at, probability, decision, threshold, production, realized, resolved_at)
- training_jobs table existence & key columns (status, trigger)
- feature_snapshot table existence & columns required for labeling (close_time, ret_1)
- risk_state / risk_positions tables (basic structure) for risk UI
Outputs a summary with PASS/FAIL per check and guidance.
"""
from __future__ import annotations
import asyncio
import asyncpg
import os
from dataclasses import dataclass
from typing import List

REQUIRED_TABLES = {
    "model_inference_log": {
        "required_cols": [
            "id","created_at","symbol","interval","model_name","model_version","probability","decision","threshold","production","realized","resolved_at"
        ],
        "optional_cols": ["extra"],
    },
    "training_jobs": {"required_cols": ["id","status","trigger","created_at"], "optional_cols": []},
    "feature_snapshot": {"required_cols": ["id","open_time","close_time","ret_1"], "optional_cols": []},
    "risk_state": {"required_cols": ["id","session_key","equity"], "optional_cols": []},
    "risk_positions": {"required_cols": ["id","session_key","symbol","qty"], "optional_cols": []},
}

@dataclass
class TableCheckResult:
    table: str
    exists: bool
    missing_columns: List[str]

async def fetch_columns(conn: asyncpg.Connection, table: str) -> List[str]:
    rows = await conn.fetch(
        """SELECT column_name FROM information_schema.columns WHERE table_name=$1""",
        table,
    )
    return [r["column_name"] for r in rows]

async def table_exists(conn: asyncpg.Connection, table: str) -> bool:
    row = await conn.fetchrow("SELECT to_regclass($1) AS reg", table)
    return row and row["reg"] is not None

async def verify(conn: asyncpg.Connection) -> List[TableCheckResult]:
    results: List[TableCheckResult] = []
    for table, spec in REQUIRED_TABLES.items():
        exists = await table_exists(conn, table)
        if not exists:
            results.append(TableCheckResult(table, False, spec["required_cols"]))
            continue
        cols = await fetch_columns(conn, table)
        missing = [c for c in spec["required_cols"] if c not in cols]
        results.append(TableCheckResult(table, True, missing))
    return results

async def main():
    dsn = os.getenv(
        "DATABASE_URL",
        f"postgres://{os.getenv('POSTGRES_USER','postgres')}:{os.getenv('POSTGRES_PASSWORD','traderpass')}@{os.getenv('POSTGRES_HOST','localhost')}:{os.getenv('POSTGRES_PORT','5432')}/{os.getenv('POSTGRES_DB','mydata')}",
    )
    print(f"Connecting: {dsn}")
    conn = await asyncpg.connect(dsn)
    try:
        results = await verify(conn)
    finally:
        await conn.close()

    print("\nSchema Verification Summary")
    print("==========================")
    overall_fail = False
    for r in results:
        if not r.exists:
            overall_fail = True
            print(f"[FAIL] {r.table}: table missing (expected cols: {', '.join(r.missing_columns)})")
        elif r.missing_columns:
            overall_fail = True
            print(f"[FAIL] {r.table}: missing columns -> {', '.join(r.missing_columns)}")
        else:
            print(f"[PASS] {r.table}")

    print("\nGuidance:")
    if overall_fail:
        print("- One or more checks failed. Run alembic upgrade head or inspect individual migrations.")
        print("- For a missing realized/resolved_at in model_inference_log ensure migration 20250929_0008 applied.")
    else:
        print("All required schema components present. Live calibration + inference logging prerequisites satisfied.")

if __name__ == "__main__":
    asyncio.run(main())

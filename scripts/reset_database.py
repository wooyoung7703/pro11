#!/usr/bin/env python
"""Full database reset utility.

Drops and recreates the configured POSTGRES_DB, then optionally recreates
known application tables (currently only news_articles is explicitly defined
in this repository). This is safer than ad‑hoc psql commands and keeps logic
in version control.

Environment variables required (or from .env):
  POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB

Flags:
    --with-news    Recreate news_articles schema after DB recreation.
    --with-all     Recreate ALL application tables (schema manager) after DB recreation.
    --only-news    Do NOT drop DB; only ensure news_articles exists in current DB.
    --only-all     Do NOT drop DB; only ensure ALL application tables.

Examples:
  python scripts/reset_database.py --with-news
  python scripts/reset_database.py --only-news

WARNING: Destroys ALL data in target DB when not using --only-news.
"""
from __future__ import annotations
import os, sys, asyncio, asyncpg, argparse, textwrap

# Allow script execution from repo root while importing backend code
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from backend.common.db.schema_manager import ensure_all as ensure_all_schema  # type: ignore
except Exception:
    ensure_all_schema = None  # type: ignore

NEWS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS news_articles (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    symbol TEXT NULL,
    title TEXT NOT NULL,
    body TEXT NULL,
    url TEXT NULL,
    published_ts BIGINT NOT NULL,
    ingested_ts BIGINT NOT NULL,
    sentiment REAL NULL,
    lang VARCHAR(8) NULL,
    hash TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_news_published_ts ON news_articles(published_ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_symbol_published_ts ON news_articles(symbol, published_ts DESC);
"""

def cfg_env(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None:
        print({"error": f"missing_env:{name}"})
        sys.exit(2)
    return v

async def ensure_news(conn: asyncpg.Connection) -> dict:  # type: ignore
    await conn.execute(NEWS_TABLE_SQL)
    cnt = await conn.fetchval("SELECT count(*) FROM news_articles")
    return {"news_articles": True, "rows": int(cnt)}

async def drop_recreate(args) -> int:
    host = cfg_env("POSTGRES_HOST", "127.0.0.1")
    port = int(cfg_env("POSTGRES_PORT", "5432"))
    user = cfg_env("POSTGRES_USER", "postgres")
    password = cfg_env("POSTGRES_PASSWORD", "traderpass")
    db = cfg_env("POSTGRES_DB", "mydata")
    # Maintenance DB (must already exist). Default should be 'postgres', not password.
    maint_db = os.getenv("POSTGRES_MAINT_DB", "postgres")

    if args.only_all:
        if ensure_all_schema is None:
            print({"error": "schema_manager_import_failed"})
            return 1
        try:
            conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database=db)
        except Exception as e:
            print({"error": f"connect_failed:{e}"})
            return 1
        try:
            # use pool-less direct ensure (schema_manager already handles pooling internally, but we call raw DDL here)
            # Fallback: open a temporary pool-like connection usage replicating ensure_all implementation
            tables: list[str] = []
            if ensure_all_schema:
                # ensure_all_schema uses init_pool internally; simplest is to call it
                tables = await ensure_all_schema()  # type: ignore
            print({"mode": "only_all", "tables": tables})
        finally:
            await conn.close()
        return 0

    if args.only_news:
        # only ensure inside existing DB
        try:
            conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database=db)
        except Exception as e:
            print({"error": f"connect_failed:{e}"})
            return 1
        try:
            out = await ensure_news(conn)
            print({"mode": "only_news", **out})
        finally:
            await conn.close()
        return 0

    # Full drop & recreate
    # Connect to a maintenance DB (try configured -> postgres -> template1)
    maint_attempts = [maint_db]
    if 'postgres' not in maint_attempts:
        maint_attempts.append('postgres')
    if 'template1' not in maint_attempts:
        maint_attempts.append('template1')
    mconn = None
    last_err = None
    for cand in maint_attempts:
        try:
            mconn = await asyncpg.connect(host=host, port=port, user=user, password=password, database=cand)
            maint_db = cand
            print({"maintenance_db": cand})
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if mconn is None:
        print({"error": f"maintenance_connect_failed:{last_err}"})
        return 1
    try:
        # terminate existing connections
        try:
            await mconn.execute("""
                SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                WHERE datname = $1 AND pid <> pg_backend_pid()
            """, db)
        except Exception:
            pass
        # Retry wrapper for drop/create operations to mitigate transient 'connection was closed' errors
        async def _retry(op_name: str, sql: str, attempts: int = 3):
            for i in range(1, attempts+1):
                try:
                    await mconn.execute(sql)
                    print({op_name: db, "attempt": i})
                    return True
                except Exception as e:  # noqa: BLE001
                    if i == attempts:
                        print({f"{op_name}_error": str(e), "attempts": attempts})
                        return False
                await asyncio.sleep(0.5 * i)
            return False
        await _retry("dropped", f"DROP DATABASE IF EXISTS {db}")
        await _retry("created", f"CREATE DATABASE {db}")
    finally:
        await mconn.close()

    if args.with_all:
        if ensure_all_schema is None:
            print({"error": "schema_manager_import_failed"})
            return 1
        try:
            conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database=db)
        except Exception as e:
            print({"error": f"connect_new_db_failed:{e}"})
            return 1
        try:
            # First pass: create all tables & base columns (idempotent)
            tables = await ensure_all_schema()  # type: ignore
            # Optional second pass (defensive) to ensure late-added columns; safe no-op if already present.
            try:
                await ensure_all_schema()  # type: ignore
            except Exception:
                pass
            # Build column summary to prove to user that all columns now exist
            col_summaries = []
            for t in tables:
                try:
                    cols = await conn.fetch(
                        "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name=$1 ORDER BY ordinal_position",
                        t,
                    )
                    col_summaries.append({
                        "table": t,
                        "column_count": len(cols),
                        "columns": [f"{r['column_name']}:{r['data_type']}:{r['is_nullable']}" for r in cols],
                    })
                except Exception as ce:  # noqa: BLE001
                    col_summaries.append({"table": t, "error": str(ce)})
            print({"initialized_all": tables, "columns_summary": col_summaries})
        finally:
            await conn.close()
    elif args.with_news:
        try:
            conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database=db)
        except Exception as e:
            print({"error": f"connect_new_db_failed:{e}"})
            return 1
        try:
            out = await ensure_news(conn)
            print({"initialized": out})
        finally:
            await conn.close()
    return 0

def parse_args(argv):
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(__doc__ or "")
    )
    p.add_argument("--with-news", action="store_true", help="After reset, ensure only news_articles table")
    p.add_argument("--with-all", action="store_true", help="After reset, ensure ALL application tables via schema manager")
    p.add_argument("--only-news", action="store_true", help="Do not drop DB; only ensure news_articles table exists")
    p.add_argument("--only-all", action="store_true", help="Do not drop DB; ensure ALL application tables")
    return p.parse_args(argv)

def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    rc = asyncio.run(drop_recreate(args))
    sys.exit(rc)

if __name__ == "__main__":
    main()

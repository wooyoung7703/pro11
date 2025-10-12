#!/usr/bin/env python
"""Reset (drop & recreate) target Postgres database and ensure news schema.

WARNING: This will DROP the database named in POSTGRES_DB. Use only in dev.

Requires superuser or a role with CREATEDB + ability to drop the target DB.
Environment variables consumed:
  POSTGRES_HOST
  POSTGRES_PORT
  POSTGRES_USER  (superuser or admin)
  POSTGRES_PASSWORD
  POSTGRES_DB    (target DB to drop & recreate)

Usage:
  python scripts/reset_news_db.py
"""
from __future__ import annotations
import os, asyncio, asyncpg, sys

HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
PORT = int(os.getenv("POSTGRES_PORT", "5432"))
USER = os.getenv("POSTGRES_USER", "postgres")
PASSWORD = os.getenv("POSTGRES_PASSWORD", "traderpass")
DBNAME = os.getenv("POSTGRES_DB", "mydata")

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

async def drop_and_create():
    # Connect to maintenance DB (postgres) to drop target
    maint_db = os.getenv("POSTGRES_MAINT_DB", "postgres")
    try:
        maint_conn = await asyncpg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=maint_db)
    except Exception as e:
        print({"error": f"maintenance_connect_failed: {e}"})
        return 1
    try:
        # Terminate existing connections
        try:
            await maint_conn.execute("""
                SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                WHERE datname = $1 AND pid <> pg_backend_pid()
            """, DBNAME)
        except Exception:
            pass
        try:
            await maint_conn.execute(f'DROP DATABASE IF EXISTS {DBNAME}')
            print({"dropped": DBNAME})
        except Exception as e:
            print({"drop_error": str(e)})
        try:
            await maint_conn.execute(f'CREATE DATABASE {DBNAME}')
            print({"created": DBNAME})
        except Exception as e:
            print({"create_error": str(e)})
    finally:
        await maint_conn.close()
    # Connect to new DB and create table
    try:
        conn = await asyncpg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DBNAME)
    except Exception as e:
        print({"error": f"connect_new_db_failed: {e}"})
        return 1
    try:
        await conn.execute(NEWS_TABLE_SQL)
        cnt = await conn.fetchval("SELECT count(*) FROM news_articles")
        print({"news_articles_exists": True, "row_count": cnt})
    finally:
        await conn.close()
    return 0

if __name__ == '__main__':
    rc = asyncio.run(drop_and_create())
    sys.exit(rc)

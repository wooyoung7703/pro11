from __future__ import annotations
import asyncio
from logging.config import fileConfig
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy import create_engine
from alembic import context
# Ensure project root on sys.path for 'backend' package import when running via alembic CLI
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load .env if present (so POSTGRES_* or DB_* variables are available during DSN injection)
dotenv_path = Path(PROJECT_ROOT) / '.env'
if dotenv_path.exists():
    load_dotenv(dotenv_path)

from backend.common.config.base_config import load_config

# this is the Alembic Config object, which provides access to the values within the .ini file in use.
config = context.config  # type: ignore

# Interpret the config file for Python logging.
if config.config_file_name is not None:  # type: ignore[attr-defined]
    fileConfig(config.config_file_name)  # type: ignore[arg-type]

app_cfg = load_config()

# Replace URL from dynamic config
raw_url = app_cfg.dsn.replace("asyncpg", "psycopg")
# Ensure we use psycopg (v3) driver explicitly; if scheme is postgresql:// swap to postgresql+psycopg://
if raw_url.startswith("postgresql://"):
    effective_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
else:
    effective_url = raw_url
config.set_main_option("sqlalchemy.url", effective_url)  # type: ignore
print(f"[alembic] Effective sqlalchemy.url -> {effective_url}")

# target_metadata can be set once models are defined
from sqlalchemy import MetaData
target_metadata = MetaData()

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")  # type: ignore
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})  # type: ignore

    with context.begin_transaction():  # type: ignore
        context.run_migrations()  # type: ignore

def run_migrations_online() -> None:
    connectable = create_engine(config.get_main_option("sqlalchemy.url"))  # type: ignore

    with connectable.connect() as connection:  # type: ignore
        context.configure(connection=connection, target_metadata=target_metadata)  # type: ignore
        with context.begin_transaction():  # type: ignore
            context.run_migrations()  # type: ignore

if context.is_offline_mode():  # type: ignore
    run_migrations_offline()
else:
    run_migrations_online()

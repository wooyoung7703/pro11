# Alembic Migrations

Usage (after installing dependencies):

```bash
poetry run alembic -c backend/migrations/alembic.ini revision -m "create kline tables"
poetry run alembic -c backend/migrations/alembic.ini upgrade head
```

The `env.py` dynamically injects the DSN from runtime config (environment variables) so you can adjust DB credentials without editing `alembic.ini`.

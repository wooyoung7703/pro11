from __future__ import annotations
from alembic import op
from sqlalchemy import text
import sqlalchemy as sa

revision = "20250929_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    timescale_available = False
    conn = op.get_bind()
    try:
        res = conn.exec_driver_sql("SELECT 1 FROM pg_available_extensions WHERE name='timescaledb'")
        if res.scalar() == 1:  # extension is installable
            try:
                conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS timescaledb;")
                timescale_available = True
            except Exception as exc:  # pragma: no cover
                print(f"[migration 20250929_0001] Could not create timescaledb extension, continuing without hypertable: {exc}")
        else:
            print("[migration 20250929_0001] timescaledb not in pg_available_extensions, skipping.")
    except Exception as exc:  # pragma: no cover
        print(f"[migration 20250929_0001] Failed to query pg_available_extensions: {exc}")

    # Raw kline table (spot/futures compatible) using hypertable
    op.create_table(
        "kline_raw",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(20), nullable=False, index=True),
        sa.Column("exchange", sa.String(20), nullable=False, server_default=sa.text("'binance'")),
        sa.Column("interval", sa.String(5), nullable=False),
        sa.Column("open_time", sa.BigInteger, nullable=False),  # ms epoch
        sa.Column("close_time", sa.BigInteger, nullable=False),
        sa.Column("open", sa.Numeric(18, 8), nullable=False),
        sa.Column("high", sa.Numeric(18, 8), nullable=False),
        sa.Column("low", sa.Numeric(18, 8), nullable=False),
        sa.Column("close", sa.Numeric(18, 8), nullable=False),
        sa.Column("volume", sa.Numeric(28, 10), nullable=False),
        sa.Column("trade_count", sa.Integer, nullable=True),
        sa.Column("taker_buy_volume", sa.Numeric(28, 10), nullable=True),
        sa.Column("taker_buy_quote_volume", sa.Numeric(28, 10), nullable=True),
        sa.Column("is_closed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("symbol", "interval", "open_time", name="uq_kline_raw_symbol_interval_open_time"),
        sa.Index("ix_kline_raw_symbol_open_time", "symbol", "open_time"),
    )

    if timescale_available:
        try:
            # Convert to hypertable (Timescale) partitioned by time (open_time) & space (symbol)
            op.execute(
                "SELECT create_hypertable('kline_raw', 'open_time', if_not_exists => TRUE, partitioning_column => 'symbol', number_partitions => 4);"
            )
        except Exception as exc:  # pragma: no cover
            print(f"[migration 20250929_0001] Failed to create hypertable, continuing as normal table: {exc}")


def downgrade() -> None:
    op.drop_table("kline_raw")
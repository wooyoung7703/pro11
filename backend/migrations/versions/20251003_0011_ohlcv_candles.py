from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20251003_0011"
down_revision = "20251002_0010_gap_segments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create canonical ohlcv_candles table (단일 테이블 전략).

    포함 사항:
      - partial/closed 캔들 겸용 (is_closed)
      - timescaledb 존재 시 hypertable 전환 시도
      - 조회 패턴 최적화를 위한 인덱스
    """
    conn = op.get_bind()
    timescale_available = False
    try:
        res = conn.exec_driver_sql("SELECT 1 FROM pg_available_extensions WHERE name='timescaledb'")
        if res.scalar() == 1:
            try:
                conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS timescaledb;")
                timescale_available = True
            except Exception as exc:  # pragma: no cover
                print(f"[migration {revision}] timescaledb extension create failed: {exc}")
    except Exception as exc:  # pragma: no cover
        print(f"[migration {revision}] extension check failed: {exc}")

    op.create_table(
        "ohlcv_candles",
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("interval", sa.String(10), nullable=False),
        sa.Column("open_time", sa.BigInteger, nullable=False),  # ms epoch
        sa.Column("close_time", sa.BigInteger, nullable=False),
        sa.Column("open", sa.Numeric(18, 8), nullable=False),
        sa.Column("high", sa.Numeric(18, 8), nullable=False),
        sa.Column("low", sa.Numeric(18, 8), nullable=False),
        sa.Column("close", sa.Numeric(18, 8), nullable=False),
        sa.Column("volume", sa.Numeric(28, 10), nullable=True),
        sa.Column("trade_count", sa.BigInteger, nullable=True),
        sa.Column("taker_buy_volume", sa.Numeric(28, 10), nullable=True),
        sa.Column("taker_buy_quote_volume", sa.Numeric(28, 10), nullable=True),
        sa.Column("is_closed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("ingestion_source", sa.String(32), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "interval", "open_time", name="pk_ohlcv_candles"),
    )

    op.create_index("ix_ohlcv_symbol_interval_time_desc", "ohlcv_candles", ["symbol", "interval", "open_time"], unique=False)
    op.create_index("ix_ohlcv_symbol_interval_closed_time_desc", "ohlcv_candles", ["symbol", "interval", "is_closed", "open_time"], unique=False)

    if timescale_available:
        try:
            op.execute(
                "SELECT create_hypertable('ohlcv_candles', 'open_time', if_not_exists => TRUE, partitioning_column => 'symbol', number_partitions => 4);"
            )
        except Exception as exc:  # pragma: no cover
            print(f"[migration {revision}] hypertable create failed: {exc}")


def downgrade() -> None:
    op.drop_index("ix_ohlcv_symbol_interval_closed_time_desc", table_name="ohlcv_candles")
    op.drop_index("ix_ohlcv_symbol_interval_time_desc", table_name="ohlcv_candles")
    op.drop_table("ohlcv_candles")

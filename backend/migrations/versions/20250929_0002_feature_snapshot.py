from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20250929_0002"
down_revision = "20250929_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_snapshot",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("interval", sa.String(5), nullable=False),
        sa.Column("open_time", sa.BigInteger, nullable=False),  # aligns with kline open_time
        sa.Column("close_time", sa.BigInteger, nullable=False),
        sa.Column("ret_1", sa.Numeric(18, 10), nullable=True),
        sa.Column("ret_5", sa.Numeric(18, 10), nullable=True),
        sa.Column("ret_10", sa.Numeric(18, 10), nullable=True),
        sa.Column("rsi_14", sa.Numeric(10, 4), nullable=True),
        sa.Column("rolling_vol_20", sa.Numeric(18, 10), nullable=True),
        sa.Column("ma_20", sa.Numeric(18, 10), nullable=True),
        sa.Column("ma_50", sa.Numeric(18, 10), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("symbol", "interval", "open_time", name="uq_feature_symbol_interval_open_time"),
        sa.Index("ix_feature_symbol_open_time", "symbol", "open_time"),
    )


def downgrade() -> None:
    op.drop_table("feature_snapshot")
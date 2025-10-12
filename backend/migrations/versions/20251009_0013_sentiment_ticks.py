from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20251009_0013"
down_revision = "20251004_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sentiment_ticks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("ts", sa.BigInteger, nullable=False),  # epoch ms
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("count", sa.Integer, nullable=True),
        sa.Column("score_raw", sa.Float, nullable=True),
        sa.Column("score_norm", sa.Float, nullable=True),
        sa.Column("score_ema_5m", sa.Float, nullable=True),
        sa.Column("score_ema_15m", sa.Float, nullable=True),
        sa.Column("meta", sa.JSON, nullable=True),
        sa.Index("ix_sentiment_symbol_ts", "symbol", "ts"),
        sa.Index("ix_sentiment_ts", "ts"),
    )


def downgrade() -> None:
    op.drop_table("sentiment_ticks")

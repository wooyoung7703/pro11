"""add backfill runs table and extend gap segments

Revision ID: 20251003_0012
Revises: 20251003_0011
Create Date: 2025-10-03
"""
from __future__ import annotations
from typing import Any
from alembic import op
import sqlalchemy as sa

revision: str = "20251003_0012"
down_revision: str | None = "20251003_0011"
branch_labels: Any = None
depends_on: Any = None


def upgrade() -> None:
    # Backfill run audit table
    op.create_table(
        "ohlcv_backfill_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("interval", sa.String(10), nullable=False),
        sa.Column("from_open_time", sa.BigInteger, nullable=False),
        sa.Column("to_open_time", sa.BigInteger, nullable=False),
        sa.Column("expected_bars", sa.BigInteger, nullable=False),
        sa.Column("loaded_bars", sa.BigInteger, server_default="0", nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),  # pending|running|success|partial|error
        sa.Column("attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_backfill_runs_symbol_interval", "ohlcv_backfill_runs", ["symbol", "interval"], unique=False)
    op.create_index("ix_backfill_runs_status", "ohlcv_backfill_runs", ["status"], unique=False)
    op.create_index("ix_backfill_runs_created_at", "ohlcv_backfill_runs", ["created_at"], unique=False)

    # Extend gap_segments with operational columns for orchestrator/backoff/merge flags
    with op.batch_alter_table("gap_segments") as batch:
        batch.add_column(sa.Column("retry_count", sa.Integer, server_default="0", nullable=False))
        batch.add_column(sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True))
        batch.add_column(sa.Column("merged", sa.Boolean, server_default=sa.text("false"), nullable=False))

    # (Optionally) future unique composite could be added if merge normalization implemented
    # op.create_unique_constraint('uq_gap_segments_span', 'gap_segments', ['symbol','interval','from_open_time','to_open_time'])


def downgrade() -> None:
    with op.batch_alter_table("gap_segments") as batch:
        batch.drop_column("merged")
        batch.drop_column("last_attempt_at")
        batch.drop_column("retry_count")
    op.drop_index("ix_backfill_runs_created_at", table_name="ohlcv_backfill_runs")
    op.drop_index("ix_backfill_runs_status", table_name="ohlcv_backfill_runs")
    op.drop_index("ix_backfill_runs_symbol_interval", table_name="ohlcv_backfill_runs")
    op.drop_table("ohlcv_backfill_runs")

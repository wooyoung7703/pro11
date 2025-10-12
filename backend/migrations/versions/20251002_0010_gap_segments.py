"""add gap_segments table

Revision ID: 20251002_0010
Revises: 20251002_0009
Create Date: 2025-10-02
"""
from __future__ import annotations
from typing import Any
from alembic import op
import sqlalchemy as sa

revision: str = "20251002_0010"
down_revision: str | None = "20251002_0009"
branch_labels: Any = None
depends_on: Any = None

def upgrade() -> None:
    op.create_table(
        "gap_segments",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("interval", sa.String(10), nullable=False),
        sa.Column("from_open_time", sa.BigInteger, nullable=False),
        sa.Column("to_open_time", sa.BigInteger, nullable=False),
        sa.Column("missing_bars", sa.Integer, nullable=False),
        sa.Column("remaining_bars", sa.Integer, nullable=False),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("recovered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("recovered_bars", sa.Integer, server_default="0", nullable=False),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),  # open|partial|recovered
    )
    op.create_index("ix_gap_segments_symbol_interval", "gap_segments", ["symbol", "interval"], unique=False)
    op.create_index("ix_gap_segments_status", "gap_segments", ["status"], unique=False)
    op.create_index("ix_gap_segments_detected_at", "gap_segments", ["detected_at"], unique=False)
    op.create_index("ix_gap_segments_from_open_time", "gap_segments", ["from_open_time"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_gap_segments_from_open_time", table_name="gap_segments")
    op.drop_index("ix_gap_segments_detected_at", table_name="gap_segments")
    op.drop_index("ix_gap_segments_status", table_name="gap_segments")
    op.drop_index("ix_gap_segments_symbol_interval", table_name="gap_segments")
    op.drop_table("gap_segments")

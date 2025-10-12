"""model inference log table

Revision ID: 20250929_0007
Revises: 20250929_0006
Create Date: 2025-09-29
"""
from __future__ import annotations
from typing import Any
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20250929_0007"
down_revision = "20250929_0006"
branch_labels: Any = None
depends_on: Any = None

def upgrade() -> None:
    op.create_table(
        "model_inference_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(16), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("probability", sa.Float, nullable=False),
        sa.Column("decision", sa.Integer, nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("production", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("extra", postgresql.JSONB, nullable=True),
        sa.Index("idx_model_inference_log_created_at", "created_at"),
        sa.Index("idx_model_inference_log_model", "model_name", "model_version"),
    )


def downgrade() -> None:
    op.drop_table("model_inference_log")

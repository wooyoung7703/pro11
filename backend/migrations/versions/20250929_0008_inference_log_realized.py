"""add realized outcome columns to inference log

Revision ID: 20250929_0008
Revises: 20250929_0007
Create Date: 2025-09-29
"""
from __future__ import annotations
from typing import Any
from alembic import op
import sqlalchemy as sa

revision = "20250929_0008"
down_revision = "20250929_0007"
branch_labels: Any = None
depends_on: Any = None

def upgrade() -> None:
    op.add_column("model_inference_log", sa.Column("realized", sa.SmallInteger(), nullable=True))
    op.add_column("model_inference_log", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("idx_model_inference_log_resolved_at", "model_inference_log", ["resolved_at"])    


def downgrade() -> None:
    op.drop_index("idx_model_inference_log_resolved_at", table_name="model_inference_log")
    op.drop_column("model_inference_log", "realized")
    op.drop_column("model_inference_log", "resolved_at")

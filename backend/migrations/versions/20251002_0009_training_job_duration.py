"""add duration columns to training_jobs

Revision ID: 20251002_0009
Revises: 20250929_0008
Create Date: 2025-10-02
"""
from __future__ import annotations
from typing import Any
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20251002_0009"
down_revision: str | None = "20250929_0008"
branch_labels: Any = None
depends_on: Any = None

def upgrade() -> None:
    op.add_column("training_jobs", sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("training_jobs", sa.Column("duration_seconds", sa.Float, nullable=True))
    op.create_index("ix_training_jobs_finished_at", "training_jobs", ["finished_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_training_jobs_finished_at", table_name="training_jobs")
    op.drop_column("training_jobs", "duration_seconds")
    op.drop_column("training_jobs", "finished_at")

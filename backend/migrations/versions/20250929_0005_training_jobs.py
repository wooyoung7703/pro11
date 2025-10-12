from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20250929_0005"
down_revision = "20250929_0004"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "training_jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("trigger", sa.String(30), nullable=False),  # manual | drift_auto
        sa.Column("drift_feature", sa.String(50), nullable=True),
        sa.Column("drift_z", sa.Float, nullable=True),
        sa.Column("artifact_path", sa.String(1000), nullable=True),
        sa.Column("model_id", sa.BigInteger, nullable=True),
        sa.Column("version", sa.String(50), nullable=True),
        sa.Column("metrics", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_training_jobs_created_at", "training_jobs", ["created_at"], unique=False)

def downgrade() -> None:
    op.drop_index("ix_training_jobs_created_at", table_name="training_jobs")
    op.drop_table("training_jobs")

from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20250929_0006"
down_revision = "20250929_0005"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "retrain_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("job_id", sa.BigInteger, nullable=True),
        sa.Column("trigger", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),  # success | error | skipped
        sa.Column("drift_feature", sa.String(50), nullable=True),
        sa.Column("drift_z", sa.Float, nullable=True),
        sa.Column("model_id", sa.BigInteger, nullable=True),
        sa.Column("version", sa.String(50), nullable=True),
        sa.Column("artifact_path", sa.String(1000), nullable=True),
        sa.Column("samples", sa.BigInteger, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_retrain_events_created_at", "retrain_events", ["created_at"], unique=False)

    op.create_table(
        "promotion_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("model_id", sa.BigInteger, nullable=True),
        sa.Column("previous_production_model_id", sa.BigInteger, nullable=True),
        sa.Column("decision", sa.String(20), nullable=False),  # promoted | skipped | error
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("samples_old", sa.BigInteger, nullable=True),
        sa.Column("samples_new", sa.BigInteger, nullable=True),
    )
    op.create_index("ix_promotion_events_created_at", "promotion_events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_promotion_events_created_at", table_name="promotion_events")
    op.drop_index("ix_retrain_events_created_at", table_name="retrain_events")
    op.drop_table("promotion_events")
    op.drop_table("retrain_events")

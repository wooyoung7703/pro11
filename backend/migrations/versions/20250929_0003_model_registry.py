from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20250929_0003"
down_revision = "20250929_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_registry",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("model_type", sa.String(30), nullable=False),  # predictor | rl_policy | feature
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'staging'")),
        sa.Column("artifact_path", sa.String(500), nullable=True),
        sa.Column("metrics", sa.JSON, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("promoted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("name", "version", "model_type", name="uq_model_name_version_type"),
        sa.Index("ix_model_name_status", "name", "status"),
    )

    op.create_table(
        "model_lineage",
        sa.Column("parent_id", sa.BigInteger, nullable=False),
        sa.Column("child_id", sa.BigInteger, nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["model_registry.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["child_id"], ["model_registry.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("parent_id", "child_id", name="uq_model_lineage_pair")
    )

    op.create_table(
        "model_metrics_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("model_id", sa.BigInteger, nullable=False),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("metrics", sa.JSON, nullable=True),
        sa.ForeignKeyConstraint(["model_id"], ["model_registry.id"], ondelete="CASCADE"),
        sa.Index("ix_model_metrics_model_id", "model_id")
    )


def downgrade() -> None:
    op.drop_table("model_metrics_history")
    op.drop_table("model_lineage")
    op.drop_table("model_registry")
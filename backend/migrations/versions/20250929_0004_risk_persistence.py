from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20250929_0004"
down_revision = "20250929_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_key", sa.String(50), nullable=False, unique=True),
        sa.Column("starting_equity", sa.Numeric(18, 4), nullable=False),
        sa.Column("peak_equity", sa.Numeric(18, 4), nullable=False),
        sa.Column("current_equity", sa.Numeric(18, 4), nullable=False),
        sa.Column("cumulative_pnl", sa.Numeric(18, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("last_reset_ts", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "risk_positions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("session_key", sa.String(50), nullable=False, index=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("size", sa.Numeric(28, 10), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("session_key", "symbol", name="uq_risk_positions_session_symbol"),
    )


def downgrade() -> None:
    op.drop_table("risk_positions")
    op.drop_table("risk_state")
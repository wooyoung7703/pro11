from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20251004_0002"
down_revision = "20251003_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    try:
        # If kline_raw was converted to a Timescale hypertable, drop chunks first (safe if not hypertable)
        try:
            conn.exec_driver_sql("SELECT drop_chunks(INTERVAL '0 days', 'kline_raw');")
        except Exception:
            pass
        # Drop table if exists
        op.execute("DROP TABLE IF EXISTS kline_raw CASCADE;")
    except Exception:
        # Non-fatal: table might not exist in some environments
        pass


def downgrade() -> None:
    # Intentionally not recreating deprecated table. If needed, recreate via earlier migration.
    pass

from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20251004_0002"
down_revision = "20251003_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
        # Use a plpgsql DO block so any failure won't abort the transaction
        # 1) If TimescaleDB is installed and function exists, try to drop chunks safely
        op.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
                        BEGIN
                            -- Attempt drop_chunks; signature may vary across versions
                            BEGIN
                                PERFORM drop_chunks(INTERVAL '0 days', 'kline_raw'::regclass);
                            EXCEPTION WHEN undefined_function THEN
                                BEGIN
                                    PERFORM timescaledb_experimental.drop_chunks(INTERVAL '0 days', 'kline_raw'::regclass);
                                EXCEPTION WHEN others THEN
                                    NULL;
                                END;
                            WHEN others THEN
                                NULL;
                            END;
                        EXCEPTION WHEN others THEN
                            NULL;
                        END;
                    END IF;
                END $$;
                """
        )

        # 2) Drop the table if it exists (works for hypertable as well)
        op.execute("DROP TABLE IF EXISTS kline_raw CASCADE;")


def downgrade() -> None:
    # Intentionally not recreating deprecated table. If needed, recreate via earlier migration.
    pass

"""Make autovacuum keep up with retention on market_trades.

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-20

Retention now deletes from `market_trades` at roughly 155M rows/day (0039 and
the global time-ordered sweep). Deleted rows do not return space to the OS;
they return it to Postgres' free-space map, and only VACUUM puts them there.
Until then the table keeps extending on disk even while rows disappear.

The default trigger is `50 + 0.2 * n_live`, which on a 322M-row table means
autovacuum waits for ~64M dead tuples — and it had never run here at all
(`autovacuum_count = 0` on 2026-09-20). At 0.02 it wakes at ~6.5M instead, so
the free-space map stays ahead of ingestion and the file stops growing.

Analyze gets the same treatment: the planner had no statistics for this table,
which is half of why the pruner chose a sequential scan for months.

Storage parameters only. Nothing here locks the table for more than the moment
it takes to update the catalog, and `downgrade` puts the defaults back.
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE market_trades SET ("
        " autovacuum_vacuum_scale_factor = 0.02,"
        " autovacuum_analyze_scale_factor = 0.02)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE market_trades RESET ("
        " autovacuum_vacuum_scale_factor,"
        " autovacuum_analyze_scale_factor)"
    )

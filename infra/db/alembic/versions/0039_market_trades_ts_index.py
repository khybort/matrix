"""Index market_trades by time alone so retention can sweep it in order.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-20

Retention pruned `market_trades` per symbol, through the existing
`(symbol, trade_ts)` index. That reads the table in the worst possible order:
the table is append-only with ~900 symbols interleaved, so one symbol's oldest
rows are scattered across the whole 97 GB heap and deleting 10 000 of them
touches 10 000 cold pages — measured three to six minutes per batch, against
~4.3M rows arriving each day. Retention lost, quietly, for months.

Taken in time order the same 10 000 rows come from ~180 contiguous pages:
measured 2026-09-20 at 20 000 rows deleted in 1.1-2.1 s. This index is what
keeps that ordered sweep cheap once the backlog is drained and the predicate
stops matching anything — without it, a global `WHERE trade_ts < cutoff LIMIT n`
degenerates into a full scan the moment there is nothing left to delete.

Built CONCURRENTLY: the table is large and live, and nothing here is worth
blocking ingestion for.
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

# CONCURRENTLY cannot run inside a transaction block.
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_market_trades_ts "
            "ON market_trades (trade_ts)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_market_trades_ts")

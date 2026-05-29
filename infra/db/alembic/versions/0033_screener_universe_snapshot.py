"""screener_universe_snapshot — exchange-wide ticker stats for universe scoring

Revision ID: 0033
Revises: 0032
Create Date: 2026-05-29

One row per USDT-perp symbol (latest poll wins). Feeds the universe manager's
liquidity/volatility/momentum/microstructure scoring so it can rank symbols
that aren't ingested yet (solves the bootstrap chicken-and-egg). LOCAL tier —
raw market data, no risk impact (same class as screener_signals).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screener_universe_snapshot",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("turnover24h", sa.Numeric(28, 2), nullable=True),
        sa.Column("volume24h", sa.Numeric(28, 4), nullable=True),
        sa.Column("price24h_pct", sa.Numeric(12, 6), nullable=True),
        sa.Column("high24h", sa.Numeric(24, 12), nullable=True),
        sa.Column("low24h", sa.Numeric(24, 12), nullable=True),
        sa.Column("last_price", sa.Numeric(24, 12), nullable=True),
        sa.Column("oi_value", sa.Numeric(24, 2), nullable=True),
        sa.Column("funding_rate", sa.Numeric(12, 8), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_screener_univ_turnover", "screener_universe_snapshot", ["turnover24h"]
    )


def downgrade() -> None:
    op.drop_table("screener_universe_snapshot")

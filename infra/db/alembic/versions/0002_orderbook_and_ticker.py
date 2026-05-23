"""orderbook snapshots + ticker snapshots

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_orderbook_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("snapshot_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bids", sa.JSON(), nullable=False),
        sa.Column("asks", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_market_ob_symbol_ts",
        "market_orderbook_snapshots",
        ["symbol", "snapshot_ts"],
    )

    op.create_table(
        "market_ticker_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("exchange", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("snapshot_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_price", sa.DECIMAL(24, 12)),
        sa.Column("mark_price", sa.DECIMAL(24, 12)),
        sa.Column("index_price", sa.DECIMAL(24, 12)),
        sa.Column("funding_rate", sa.DECIMAL(18, 10)),
        sa.Column("next_funding_ts", sa.DateTime(timezone=True)),
        sa.Column("open_interest", sa.DECIMAL(28, 8)),
        sa.Column("volume_24h", sa.DECIMAL(28, 8)),
        sa.Column("turnover_24h", sa.DECIMAL(28, 8)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_market_ticker_symbol_ts",
        "market_ticker_snapshots",
        ["symbol", "snapshot_ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_ticker_symbol_ts", table_name="market_ticker_snapshots")
    op.drop_table("market_ticker_snapshots")
    op.drop_index("ix_market_ob_symbol_ts", table_name="market_orderbook_snapshots")
    op.drop_table("market_orderbook_snapshots")

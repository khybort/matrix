"""wallets + wallet_snapshots; paper_positions.wallet_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-23

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_WALLET_ID = uuid.UUID("00000000-0000-0000-0000-00000000d0e1")


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("starting_capital_usd", sa.DECIMAL(18, 4), nullable=False, server_default="10000"),
        sa.Column("cash_usd", sa.DECIMAL(18, 6), nullable=False, server_default="10000"),
        sa.Column("locked_usd", sa.DECIMAL(18, 6), nullable=False, server_default="0"),
        sa.Column("max_position_pct", sa.DECIMAL(6, 4), nullable=False, server_default="0.02"),
        sa.Column("max_concurrent_positions", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("daily_loss_circuit_pct", sa.DECIMAL(6, 4), nullable=False, server_default="0.05"),
        sa.Column("circuit_tripped_at", sa.DateTime(timezone=True)),
        sa.Column("day_start_equity", sa.DECIMAL(18, 6), nullable=False, server_default="10000"),
        sa.Column("day_start_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "wallet_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity_usd", sa.DECIMAL(18, 6), nullable=False),
        sa.Column("cash_usd", sa.DECIMAL(18, 6), nullable=False),
        sa.Column("locked_usd", sa.DECIMAL(18, 6), nullable=False),
        sa.Column("n_open_positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("realized_pnl_usd", sa.DECIMAL(18, 6), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl_usd", sa.DECIMAL(18, 6), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_wallet_snapshots_wallet_ts", "wallet_snapshots", ["wallet_id", "snapshot_ts"])

    # Existing paper_positions: add wallet_id (nullable for backfill, then NOT NULL).
    # Since current data is paper-only, we can wipe + add NOT NULL.
    op.execute("DELETE FROM paper_positions; DELETE FROM outcomes;")
    op.add_column(
        "paper_positions",
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Seed default wallet
    op.execute(
        sa.text(
            "INSERT INTO wallets (id, name, starting_capital_usd, cash_usd, "
            "max_position_pct, max_concurrent_positions, daily_loss_circuit_pct, "
            "day_start_equity) "
            "VALUES (:id, :name, :cap, :cap, :mpp, :mcp, :dlc, :cap)"
        ).bindparams(
            id=DEFAULT_WALLET_ID,
            name="default",
            cap=Decimal("10000"),
            mpp=Decimal("0.02"),
            mcp=5,
            dlc=Decimal("0.05"),
        )
    )

    # Backfill (no-op since we just wiped) then enforce NOT NULL + FK
    op.alter_column("paper_positions", "wallet_id", nullable=False)
    op.create_foreign_key(
        "fk_paper_positions_wallet",
        "paper_positions",
        "wallets",
        ["wallet_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_paper_positions_wallet", "paper_positions", type_="foreignkey")
    op.drop_column("paper_positions", "wallet_id")
    op.drop_index("ix_wallet_snapshots_wallet_ts", table_name="wallet_snapshots")
    op.drop_table("wallet_snapshots")
    op.drop_table("wallets")

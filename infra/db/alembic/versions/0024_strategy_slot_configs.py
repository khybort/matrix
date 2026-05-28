"""Add strategy_slot_configs table.

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_slot_configs",
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("asset_class", sa.Text(), nullable=False),
        sa.Column("wallet_id", UUID(as_uuid=True), nullable=False),
        sa.Column("allocated_slots", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("perf_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("consecutive_losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("strategy_id", "asset_class", "wallet_id"),
    )
    op.create_index("ix_ssc_wallet", "strategy_slot_configs", ["wallet_id"])


def downgrade() -> None:
    op.drop_index("ix_ssc_wallet", table_name="strategy_slot_configs")
    op.drop_table("strategy_slot_configs")

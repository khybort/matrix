"""Add tradable_symbols universe table.

Revision ID: 0032
Revises: 0031
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tradable_symbols",
        sa.Column("asset_class", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("components_json", sa.JSON(), nullable=True),
        sa.Column("liquidity_usd", sa.DECIMAL(precision=28, scale=2), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("became_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("asset_class", "symbol"),
    )
    op.create_index("ix_tradable_active", "tradable_symbols", ["asset_class", "active"])
    op.create_index("ix_tradable_score", "tradable_symbols", ["asset_class", "score"])


def downgrade() -> None:
    op.drop_index("ix_tradable_score", table_name="tradable_symbols")
    op.drop_index("ix_tradable_active", table_name="tradable_symbols")
    op.drop_table("tradable_symbols")

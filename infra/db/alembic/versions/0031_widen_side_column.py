"""Widen predictions.side and paper_positions.side from VARCHAR(8) to VARCHAR(16).

The cash_and_carry strategy uses side='delta_neutral' (13 chars) which
exceeds the original VARCHAR(8) limit. BIST and crypto directional values
('long', 'short', 'flat') remain well within the new limit.

Revision ID: 0031
Revises: 0030
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "predictions",
        "side",
        existing_type=sa.String(8),
        type_=sa.String(16),
        existing_nullable=False,
    )
    op.alter_column(
        "paper_positions",
        "side",
        existing_type=sa.String(8),
        type_=sa.String(16),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Only safe if no rows contain values longer than 8 chars.
    op.alter_column(
        "paper_positions",
        "side",
        existing_type=sa.String(16),
        type_=sa.String(8),
        existing_nullable=False,
    )
    op.alter_column(
        "predictions",
        "side",
        existing_type=sa.String(16),
        type_=sa.String(8),
        existing_nullable=False,
    )

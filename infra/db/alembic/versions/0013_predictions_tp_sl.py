"""predictions: per-trade take-profit / stop-loss thresholds

Today, every paper-trade position closes only when its prediction's
`close_by` horizon elapses. That's correct for evaluating the strategy's
horizon hypothesis but it lets profits evaporate and losses compound during
the wait. tp_pct / sl_pct give the strategy layer a way to attach
sign-aware close-early thresholds per prediction without inventing a new
table or a strategy-level config schema.

Semantics: both columns are NULL by default — preserves today's
horizon-only behavior. When set:
    long  : close when (price_now / opened_price - 1) >= tp_pct  (profit)
    long  : close when (1 - price_now / opened_price) >= sl_pct  (loss)
    short : mirrored
The closer also keeps the horizon check — whichever fires first wins.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS tp_pct NUMERIC(8,6)"
    )
    op.execute(
        "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS sl_pct NUMERIC(8,6)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE predictions DROP COLUMN sl_pct")
    op.execute("ALTER TABLE predictions DROP COLUMN tp_pct")

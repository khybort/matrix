"""Raise max_concurrent_positions: crypto 15→50, BIST 3→20.

Per-strategy slot configs are now the binding constraint. The wallet cap
should equal floor(1/max_position_pct) so capital, not count, is the limit.
  crypto: max_position_pct=2%  → 50 slots
  bist:   max_position_pct=5%  → 20 slots

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE wallets SET max_concurrent_positions = 50 WHERE asset_class = 'crypto'"
    )
    op.execute(
        "UPDATE wallets SET max_concurrent_positions = 20 WHERE asset_class = 'bist'"
    )
    op.execute(
        "ALTER TABLE wallets ALTER COLUMN max_concurrent_positions SET DEFAULT 50"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE wallets SET max_concurrent_positions = 15 WHERE asset_class = 'crypto'"
    )
    op.execute(
        "UPDATE wallets SET max_concurrent_positions = 3 WHERE asset_class = 'bist'"
    )
    op.execute(
        "ALTER TABLE wallets ALTER COLUMN max_concurrent_positions SET DEFAULT 15"
    )

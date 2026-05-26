"""agent_lessons: asset_class column + per-market index

Without an `asset_class` discriminator the synthesizer collapses crypto
and BIST observations under the same (strategy_id, symbol, side) bucket,
which is wrong: a "long on THYAO" lesson must never inform a crypto
decision (and vice versa). Phase E walks `all_markets()` and writes one
lesson row per (strategy_id, asset_class) pair.

Existing rows backfill to 'crypto' — every active lesson today was
synthesized from matrix_agent on crypto symbols.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_lessons ADD COLUMN IF NOT EXISTS "
        "asset_class VARCHAR(16) NOT NULL DEFAULT 'crypto'"
    )
    # Per-market active-lesson lookups (synthesizer + feeder both filter
    # by strategy_id + asset_class + status).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_lessons_strategy_market_status "
        "ON agent_lessons(strategy_id, asset_class, status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_lessons_strategy_market_status")
    op.execute("ALTER TABLE agent_lessons DROP COLUMN IF EXISTS asset_class")

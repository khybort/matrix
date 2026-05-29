"""Seed screener_follow strategy config + slot.

Revision ID: 0029
Revises: 0026
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Crypto wallet UUID (seeds in 0004 / 0010)
_CRYPTO_WALLET_ID = "00000000-0000-0000-0000-00000000d0e1"


def upgrade() -> None:
    op.execute("""
        INSERT INTO strategy_configs
            (id, strategy_id, asset_class, version, params, status, rationale, promoted_at)
        VALUES (
            '00000000-0000-0000-0000-000000d0e129'::uuid,
            'screener_follow',
            'crypto',
            1,
            '{"min_passes": 3, "min_score": "0.05", "horizon_s": 1800}'::json,
            'active',
            'Initial seed: screener_follow turns OI/funding screener candidates into trades.',
            now()
        )
        ON CONFLICT DO NOTHING
    """)

    op.execute(f"""
        INSERT INTO strategy_slot_configs
            (strategy_id, asset_class, wallet_id, allocated_slots, perf_score, consecutive_losses)
        VALUES (
            'screener_follow',
            'crypto',
            '{_CRYPTO_WALLET_ID}',
            4,
            0.5,
            0
        )
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute(
        "DELETE FROM strategy_slot_configs WHERE strategy_id = 'screener_follow'"
    )
    op.execute(
        "DELETE FROM strategy_configs WHERE strategy_id = 'screener_follow'"
    )

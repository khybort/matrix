"""Seed momentum_xs strategy config and slot config.

Inserts:
  1. strategy_configs row — status=active, v1, asset_class=crypto.
  2. strategy_slot_configs row — crypto default wallet, 6 slots, perf_score=0.5.

Revision ID: 0028
Revises: 0026
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CRYPTO_WALLET_ID = "00000000-0000-0000-0000-00000000d0e1"


def upgrade() -> None:
    op.execute("""
        INSERT INTO strategy_configs (strategy_id, asset_class, version, status, params, rationale)
        VALUES (
            'momentum_xs',
            'crypto',
            1,
            'active',
            '{"lookback_days": 7, "top_k": 3, "vol_filter_pct": "0.5", "horizon_s": 3600}'::json,
            'initial seed'
        )
        ON CONFLICT DO NOTHING
    """)

    op.execute(f"""
        INSERT INTO strategy_slot_configs (strategy_id, asset_class, wallet_id, allocated_slots, perf_score, consecutive_losses)
        VALUES (
            'momentum_xs',
            'crypto',
            '{_CRYPTO_WALLET_ID}'::uuid,
            6,
            0.5,
            0
        )
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute(f"""
        DELETE FROM strategy_slot_configs
        WHERE strategy_id = 'momentum_xs' AND wallet_id = '{_CRYPTO_WALLET_ID}'::uuid
    """)
    op.execute("""
        DELETE FROM strategy_configs
        WHERE strategy_id = 'momentum_xs' AND asset_class = 'crypto'
    """)

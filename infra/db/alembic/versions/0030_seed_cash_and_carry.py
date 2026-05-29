"""Seed cash_and_carry strategy config and slot config.

Inserts:
  1. strategy_configs row — status=active, v1, asset_class=crypto.
  2. strategy_slot_configs row — crypto default wallet, 5 slots, perf_score=0.5.

Revision ID: 0030
Revises: 0029
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CRYPTO_WALLET_ID = "00000000-0000-0000-0000-00000000d0e1"


def upgrade() -> None:
    op.execute("""
        INSERT INTO strategy_configs (id, strategy_id, asset_class, version, status, params, rationale)
        VALUES (
            '00000000-0000-0000-0000-000000d0e130'::uuid,
            'cash_and_carry',
            'crypto',
            1,
            'active',
            '{"min_funding": "0.0001", "horizon_s": 28800}'::json,
            'initial seed: delta-neutral funding capture (spot-long + perp-short synthetic)'
        )
        ON CONFLICT DO NOTHING
    """)

    op.execute(f"""
        INSERT INTO strategy_slot_configs (strategy_id, asset_class, wallet_id, allocated_slots, perf_score, consecutive_losses)
        VALUES (
            'cash_and_carry',
            'crypto',
            '{_CRYPTO_WALLET_ID}'::uuid,
            5,
            0.5,
            0
        )
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute(f"""
        DELETE FROM strategy_slot_configs
        WHERE strategy_id = 'cash_and_carry' AND wallet_id = '{_CRYPTO_WALLET_ID}'::uuid
    """)
    op.execute("""
        DELETE FROM strategy_configs
        WHERE strategy_id = 'cash_and_carry' AND asset_class = 'crypto'
    """)

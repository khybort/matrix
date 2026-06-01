"""Re-enable retired crypto strategies + headroom for parallel paper analysis.

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-01

Inserts strategy_configs + slots for dca and oi_breakout (removed from the
registry 2026-05-28 for poor PnL — re-opened for comparative analysis).
Raises crypto wallet max_concurrent_positions 50 → 80 so new strategies
aren't starved while matrix_agent holds most slots.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CRYPTO_WALLET_ID = "00000000-0000-0000-0000-00000000d0e1"


def upgrade() -> None:
    op.execute("""
        UPDATE wallets
        SET max_concurrent_positions = 80
        WHERE asset_class = 'crypto' AND name = 'default'
          AND max_concurrent_positions < 80
    """)

    op.execute("""
        INSERT INTO strategy_configs (id, strategy_id, asset_class, version, status, params, rationale)
        SELECT * FROM (VALUES
            (
                '00000000-0000-0000-0000-00000000d120'::uuid,
                'dca',
                'crypto',
                1,
                'active',
                '{"interval_minutes": 60, "horizon_s": 3600}'::json,
                're-enabled for comparative paper analysis (2026-06-01)'
            ),
            (
                '00000000-0000-0000-0000-00000000d121'::uuid,
                'oi_breakout',
                'crypto',
                1,
                'active',
                '{"oi_threshold_pct": "0.020", "horizon_s": 1800, "tp_pct": "0.020", "sl_pct": "0.010"}'::json,
                're-enabled for comparative paper analysis (2026-06-01)'
            )
        ) AS v(id, strategy_id, asset_class, version, status, params, rationale)
        WHERE NOT EXISTS (
            SELECT 1 FROM strategy_configs sc
            WHERE sc.strategy_id = v.strategy_id AND sc.asset_class = v.asset_class AND sc.status = 'active'
        )
    """)

    for sid, slots in (("dca", 3), ("oi_breakout", 3)):
        op.execute(f"""
            INSERT INTO strategy_slot_configs
                (strategy_id, asset_class, wallet_id, allocated_slots, perf_score, consecutive_losses)
            VALUES ('{sid}', 'crypto', '{_CRYPTO_WALLET_ID}'::uuid, {slots}, 0.5, 0)
            ON CONFLICT DO NOTHING
        """)


def downgrade() -> None:
    for sid in ("dca", "oi_breakout"):
        op.execute(f"DELETE FROM strategy_slot_configs WHERE strategy_id = '{sid}'")
        op.execute(f"DELETE FROM strategy_configs WHERE strategy_id = '{sid}'")
    op.execute("""
        UPDATE wallets SET max_concurrent_positions = 50
        WHERE asset_class = 'crypto' AND name = 'default'
    """)

"""Seed strategy_configs + slot_configs for registered-but-missing strategies.

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-01

Migrations 0026/0027 only UPDATE existing rows; grid, funding_reversion,
oi_delta, and all BIST deterministic modules were never INSERTed. The
strategy dispatcher skips any module without an active strategy_configs row.

Inserts (idempotent via NOT EXISTS / ON CONFLICT):
  Crypto: grid, funding_reversion, oi_delta
  BIST:   bist_gap_fade, bist_intraday_reversion, bist_volume_breakout,
          bist_news_event
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CRYPTO_WALLET_ID = "00000000-0000-0000-0000-00000000d0e1"


def upgrade() -> None:
    # Allow one active config per (strategy_id, asset_class, version) — e.g.
    # matrix_agent crypto v1 + matrix_agent bist v1.
    op.drop_constraint("uq_strategy_configs_id_ver", "strategy_configs", type_="unique")
    op.create_unique_constraint(
        "uq_strategy_configs_id_class_ver",
        "strategy_configs",
        ["strategy_id", "asset_class", "version"],
    )

    # ── crypto strategy_configs ──────────────────────────────────────────
    op.execute("""
        INSERT INTO strategy_configs (id, strategy_id, asset_class, version, status, params, rationale)
        SELECT * FROM (VALUES
            (
                '00000000-0000-0000-0000-00000000d110'::uuid,
                'grid',
                'crypto',
                1,
                'active',
                '{"n_grids": 10, "price_band_pct": "0.02", "horizon_s": 300, "tp_pct": "0.010", "sl_pct": "0.015"}'::json,
                'initial seed: grid mean-reversion bands'
            ),
            (
                '00000000-0000-0000-0000-00000000d111'::uuid,
                'funding_reversion',
                'crypto',
                1,
                'active',
                '{"high_funding": "0.00015", "funding_cap": "0.0005", "horizon_s": 600, "tp_pct": "0.010", "sl_pct": "0.005"}'::json,
                'initial seed: funding-rate fade'
            ),
            (
                '00000000-0000-0000-0000-00000000d112'::uuid,
                'oi_delta',
                'crypto',
                1,
                'active',
                '{"oi_threshold_pct": "0.015", "horizon_s": 300, "tp_pct": "0.015", "sl_pct": "0.0075"}'::json,
                'initial seed: OI delta momentum'
            )
        ) AS v(id, strategy_id, asset_class, version, status, params, rationale)
        WHERE NOT EXISTS (
            SELECT 1 FROM strategy_configs sc
            WHERE sc.strategy_id = v.strategy_id AND sc.asset_class = v.asset_class AND sc.status = 'active'
        )
    """)

    # ── BIST strategy_configs ────────────────────────────────────────────
    op.execute("""
        INSERT INTO strategy_configs (id, strategy_id, asset_class, version, status, params, rationale)
        SELECT * FROM (VALUES
            (
                '00000000-0000-0000-0000-00000000d200'::uuid,
                'bist_gap_fade',
                'bist',
                1,
                'active',
                '{"gap_threshold": "0.015", "gap_cap": "0.05", "horizon_s": 1800, "tp_pct": "0.020", "sl_pct": "0.010"}'::json,
                'initial seed: opening gap fade (long-only)'
            ),
            (
                '00000000-0000-0000-0000-00000000d201'::uuid,
                'bist_intraday_reversion',
                'bist',
                1,
                'active',
                '{"drop_threshold": "0.03", "drop_cap": "0.07", "horizon_s": 3600, "tp_pct": "0.025", "sl_pct": "0.015"}'::json,
                'initial seed: intraday drop reversion'
            ),
            (
                '00000000-0000-0000-0000-00000000d202'::uuid,
                'bist_volume_breakout',
                'bist',
                1,
                'active',
                '{"vol_mult": "3.0", "vol_mult_cap": "8.0", "horizon_s": 900, "tp_pct": "0.030", "sl_pct": "0.015"}'::json,
                'initial seed: volume breakout momentum'
            ),
            (
                '00000000-0000-0000-0000-00000000d203'::uuid,
                'bist_news_event',
                'bist',
                1,
                'active',
                '{"horizon_s": 3600, "tp_pct": "0.030", "sl_pct": "0.015"}'::json,
                'initial seed: KAP/news headline reaction'
            )
        ) AS v(id, strategy_id, asset_class, version, status, params, rationale)
        WHERE NOT EXISTS (
            SELECT 1 FROM strategy_configs sc
            WHERE sc.strategy_id = v.strategy_id AND sc.asset_class = v.asset_class AND sc.status = 'active'
        )
    """)

    # ── BIST matrix_agent (0008 seed blocked by old unique constraint) ───
    op.execute("""
        INSERT INTO strategy_configs (id, strategy_id, asset_class, version, status, params, rationale)
        SELECT
            '00000000-0000-0000-0000-00000000b157'::uuid,
            'matrix_agent',
            'bist',
            1,
            'active',
            '{"weights": {"trade_flow": "0.45", "funding": "0", "oi_delta": "0", "ob_imbalance": "0.20", "news": "0.35"}, "signal_threshold": "0.18", "horizon_seconds": 3600, "tp_pct": "0.025", "sl_pct": "0.015", "explore_epsilon": 0.05}'::json,
            'initial BIST seed; crypto-only signals zeroed'
        WHERE NOT EXISTS (
            SELECT 1 FROM strategy_configs sc
            WHERE sc.strategy_id = 'matrix_agent' AND sc.asset_class = 'bist' AND sc.status = 'active'
        )
    """)

    # ── crypto slot_configs ──────────────────────────────────────────────
    for sid, slots in (
        ("grid", 4),
        ("funding_reversion", 4),
        ("oi_delta", 4),
    ):
        op.execute(f"""
            INSERT INTO strategy_slot_configs
                (strategy_id, asset_class, wallet_id, allocated_slots, perf_score, consecutive_losses)
            VALUES ('{sid}', 'crypto', '{_CRYPTO_WALLET_ID}'::uuid, {slots}, 0.5, 0)
            ON CONFLICT DO NOTHING
        """)

    # ── BIST slot_configs (resolve wallet by asset_class) ────────────────
    for sid in (
        "bist_gap_fade",
        "bist_intraday_reversion",
        "bist_volume_breakout",
        "bist_news_event",
    ):
        op.execute(f"""
            INSERT INTO strategy_slot_configs
                (strategy_id, asset_class, wallet_id, allocated_slots, perf_score, consecutive_losses)
            SELECT '{sid}', 'bist', w.id, 1, 0.5, 0
            FROM wallets w
            WHERE w.asset_class = 'bist' AND w.name = 'default'
            LIMIT 1
            ON CONFLICT DO NOTHING
        """)


def downgrade() -> None:
    op.execute(
        "DELETE FROM strategy_configs WHERE strategy_id = 'matrix_agent' AND asset_class = 'bist'"
    )
    for sid in (
        "grid",
        "funding_reversion",
        "oi_delta",
        "bist_gap_fade",
        "bist_intraday_reversion",
        "bist_volume_breakout",
        "bist_news_event",
    ):
        op.execute(f"DELETE FROM strategy_slot_configs WHERE strategy_id = '{sid}'")
        op.execute(f"DELETE FROM strategy_configs WHERE strategy_id = '{sid}'")

    op.drop_constraint("uq_strategy_configs_id_class_ver", "strategy_configs", type_="unique")
    op.create_unique_constraint(
        "uq_strategy_configs_id_ver",
        "strategy_configs",
        ["strategy_id", "version"],
    )

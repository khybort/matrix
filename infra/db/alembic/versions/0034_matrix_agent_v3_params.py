"""matrix_agent v3 params — long horizon + oi_delta/news weights.

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-01

Aligns live config with CHANGES.md diagnosis (2026-05-26): direction works
at 1800s+; oi_delta + news are the only near-break-even features.
Only updates active matrix_agent rows still on short horizons / legacy weights.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_V3_PARAMS = """{
  "weights": {
    "trade_flow": "0.05",
    "funding": "0.05",
    "oi_delta": "0.40",
    "ob_imbalance": "0.05",
    "news": "0.40"
  },
  "signal_threshold": "0.15",
  "horizon_seconds": 1800,
  "tp_pct": "0.02",
  "sl_pct": "0.01",
  "explore_epsilon": 0.05
}"""


def upgrade() -> None:
    # Upgrade legacy v1/v2 configs: short horizon or pre-v3 weight mix.
    op.execute(f"""
        UPDATE strategy_configs
        SET params = '{_V3_PARAMS}'::json,
            rationale = COALESCE(rationale, '') || ' [migration 0034: v3 long-horizon params]'
        WHERE strategy_id = 'matrix_agent'
          AND status = 'active'
          AND asset_class = 'crypto'
          AND (
            (params->>'horizon_seconds') IS NULL
            OR (params->>'horizon_seconds')::int < 600
            OR COALESCE((params->'weights'->>'oi_delta')::numeric, 0) < 0.30
          )
    """)


def downgrade() -> None:
    pass

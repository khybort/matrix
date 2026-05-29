"""Seed default params for funding_reversion + BIST strategies.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-29

These strategies currently sit at params={} in strategy_configs. The
reflection param_tune loop can't mutate them without a starting point.

Exact values match the module-level constants so the seeded state is
identical to what the strategy computes when no override is present:

  funding_reversion:      high_funding=0.0002, funding_cap=0.0005, horizon_s=600
  bist_gap_fade:          gap_threshold=0.015, gap_cap=0.05, horizon_s=1800
  bist_intraday_reversion: drop_threshold=0.03, drop_cap=0.07, horizon_s=3600
  bist_volume_breakout:   vol_mult=3.0, vol_mult_cap=8.0, horizon_s=900

Only seeds when params is NULL or empty — never overwrites operator tunings.
"""

from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only seed when params is empty so we don't overwrite operator tunings.
    op.execute("""
        UPDATE strategy_configs
        SET params = '{"high_funding": "0.0002", "funding_cap": "0.0005", "horizon_s": 600}'::json
        WHERE strategy_id = 'funding_reversion' AND status = 'active'
          AND (params::text = '{}' OR params IS NULL)
    """)
    op.execute("""
        UPDATE strategy_configs
        SET params = '{"gap_threshold": "0.015", "gap_cap": "0.05", "horizon_s": 1800}'::json
        WHERE strategy_id = 'bist_gap_fade' AND status = 'active'
          AND (params::text = '{}' OR params IS NULL)
    """)
    op.execute("""
        UPDATE strategy_configs
        SET params = '{"drop_threshold": "0.03", "drop_cap": "0.07", "horizon_s": 3600}'::json
        WHERE strategy_id = 'bist_intraday_reversion' AND status = 'active'
          AND (params::text = '{}' OR params IS NULL)
    """)
    op.execute("""
        UPDATE strategy_configs
        SET params = '{"vol_mult": "3.0", "vol_mult_cap": "8.0", "horizon_s": 900}'::json
        WHERE strategy_id = 'bist_volume_breakout' AND status = 'active'
          AND (params::text = '{}' OR params IS NULL)
    """)


def downgrade() -> None:
    # No-op — seeded defaults remain; operator can edit manually.
    pass

"""Seed default params for deterministic strategies.

Grid/DCA/OI-delta currently sit at params={} in strategy_configs. Reflection's
rule_propose can't mutate them because there's nothing to perturb. Seed
realistic defaults so the param_tune loop has a starting point.

Grid defaults:   n_grids=10, price_band_pct=0.02, horizon_s=300
DCA defaults:    interval_minutes=60
OI-delta:        oi_threshold_pct=0.015 (maps to OI_JUMP_THRESHOLD), horizon_s=300

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-29
"""

from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only seed when params is empty so we don't overwrite operator tunings.
    # The params column is json (not jsonb), so compare via ::text.
    op.execute("""
        UPDATE strategy_configs
        SET params = '{"n_grids": 10, "price_band_pct": "0.02", "horizon_s": 300}'::json
        WHERE strategy_id = 'grid' AND status = 'active'
          AND (params::text = '{}' OR params IS NULL)
    """)
    op.execute("""
        UPDATE strategy_configs
        SET params = '{"interval_minutes": 60}'::json
        WHERE strategy_id = 'dca' AND status = 'active'
          AND (params::text = '{}' OR params IS NULL)
    """)
    op.execute("""
        UPDATE strategy_configs
        SET params = '{"oi_threshold_pct": "0.015", "horizon_s": 300}'::json
        WHERE strategy_id = 'oi_delta' AND status = 'active'
          AND (params::text = '{}' OR params IS NULL)
    """)


def downgrade() -> None:
    # No-op — seeded defaults remain, but operator can edit manually.
    pass

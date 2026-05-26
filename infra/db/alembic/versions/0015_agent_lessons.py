"""agent_lessons — pattern-level lessons distilled from past decisions

Reflection's `mutation_proposals` mutate strategy_configs at the param
level (re-weight, retune thresholds). This table captures something
finer: "decisions you made in pattern X have win rate Y over n trades."
The agent reads these on every tick (once wired) and can suppress or
amplify signals when a known anti-pattern matches the current feature
state — without changing its configured weights.

Why a separate table from dev_agent_lessons:
- different audience (matrix_agent vs dev_agent)
- different shape (feature-value bands vs code patches)
- different lifecycle (continuous synthesis vs human approval)

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_lessons (
          id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          strategy_id          varchar(64)  NOT NULL,
          strategy_version     integer      NOT NULL,
          pattern_kind         varchar(32)  NOT NULL,
          pattern_description  text         NOT NULL,
          pattern_filter       jsonb        NOT NULL,
          n_observations       integer      NOT NULL,
          win_rate             numeric(8,6),
          avg_pnl_usd          numeric(18,6),
          total_pnl_usd        numeric(18,6),
          verdict              varchar(16)  NOT NULL,
          confidence           numeric(6,4),
          observed_from        timestamptz  NOT NULL,
          observed_until       timestamptz  NOT NULL,
          generated_at         timestamptz  NOT NULL DEFAULT NOW(),
          status               varchar(16)  NOT NULL DEFAULT 'active',
          superseded_by        uuid REFERENCES agent_lessons(id),
          created_at           timestamptz  NOT NULL DEFAULT NOW(),
          updated_at           timestamptz  NOT NULL DEFAULT NOW(),
          CONSTRAINT ck_agent_lessons_verdict
            CHECK (verdict IN ('avoid', 'prefer', 'neutral')),
          CONSTRAINT ck_agent_lessons_status
            CHECK (status IN ('active', 'superseded', 'expired'))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_lessons_active "
        "ON agent_lessons(strategy_id, status, pattern_kind)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_lessons_generated "
        "ON agent_lessons(generated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_lessons")

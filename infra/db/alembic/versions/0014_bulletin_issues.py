"""bulletin_issues — weekly digest archive

Auto-generated content (LLM-composed from engine state) lives here.
v0: no subscriber table, no email delivery. Published = visible at
/bulletin. Subscription is a Phase 6.5 follow-up that can grow on
top without schema changes here.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS bulletin_issues (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          slug            varchar(64) NOT NULL UNIQUE,
          title           text        NOT NULL,
          summary         text,
          body_md         text        NOT NULL,
          issue_date      date        NOT NULL,
          status          varchar(16) NOT NULL DEFAULT 'draft',
          model           varchar(64),
          model_cost_usd  numeric(10,6),
          generated_at    timestamptz NOT NULL DEFAULT NOW(),
          published_at    timestamptz,
          created_at      timestamptz NOT NULL DEFAULT NOW(),
          updated_at      timestamptz NOT NULL DEFAULT NOW(),
          CONSTRAINT ck_bulletin_status CHECK (status IN ('draft', 'published', 'archived'))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bulletin_issues_status_date "
        "ON bulletin_issues(status, issue_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bulletin_issues")

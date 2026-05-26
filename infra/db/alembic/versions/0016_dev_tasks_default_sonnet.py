"""dev_tasks.model default → claude-sonnet-4-6

Project-wide subscription LLM switch on 2026-05-26. Existing rows keep
whatever they had; only the COLUMN DEFAULT changes for future inserts.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE dev_tasks ALTER COLUMN model SET DEFAULT 'claude-sonnet-4-6'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE dev_tasks ALTER COLUMN model SET DEFAULT 'claude-haiku-4-5'"
    )

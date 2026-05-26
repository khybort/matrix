"""Bulletin issue — auto-generated weekly digest visible at /bulletin.

Decoupled from any subscriber model: publishing here just means status =
'published'. Email delivery would consume from this table; v0 doesn't
include that.
"""

from __future__ import annotations

import uuid
from datetime import date as _date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from matrix_shared.models.base import Base, TimestampMixin


class BulletinIssue(Base, TimestampMixin):
    __tablename__ = "bulletin_issues"
    __table_args__ = (
        UniqueConstraint("slug", name="bulletin_issues_slug_key"),
        Index("ix_bulletin_issues_status_date", "status", "issue_date"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_bulletin_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    issue_date: Mapped[_date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft"
    )
    model: Mapped[str | None] = mapped_column(String(64))
    model_cost_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 6))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

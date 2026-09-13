"""Shared US-strategy helpers: session check, symbol load, bar queries."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared.markets import us_calendar
from matrix_shared.markets.us_calendar import NY
from matrix_shared.models import MarketBar, UsSymbol

US_EXCHANGE = "US"
ASSET_CLASS = "us"


def in_session(now: datetime | None = None) -> bool:
    """True if now is within the US regular cash session (NYSE calendar)."""
    return us_calendar.is_session_open(now)


def session_open_utc(now_utc: datetime) -> datetime:
    """Today's 09:30 America/New_York (regular open) expressed in UTC."""
    now_ny = now_utc.astimezone(NY)
    open_ny = now_ny.replace(
        hour=us_calendar.REGULAR_OPEN.hour,
        minute=us_calendar.REGULAR_OPEN.minute,
        second=0,
        microsecond=0,
    )
    return open_ny.astimezone(UTC)


async def active_us_symbols(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        select(UsSymbol.symbol).where(UsSymbol.active.is_(True))
    )
    return sorted({r[0] for r in rows})


async def latest_bar(
    session: AsyncSession, symbol: str, *, interval: str
) -> MarketBar | None:
    stmt = (
        select(MarketBar)
        .where(MarketBar.symbol == symbol)
        .where(MarketBar.asset_class == ASSET_CLASS)
        .where(MarketBar.interval == interval)
        .order_by(desc(MarketBar.ts))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def recent_bars(
    session: AsyncSession, symbol: str, *, interval: str, n: int
) -> list[MarketBar]:
    stmt = (
        select(MarketBar)
        .where(MarketBar.symbol == symbol)
        .where(MarketBar.asset_class == ASSET_CLASS)
        .where(MarketBar.interval == interval)
        .order_by(desc(MarketBar.ts))
        .limit(n)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(reversed(rows))  # chronological order


def safe_pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator

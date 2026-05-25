"""Shared BIST-strategy helpers: session check, symbol load, bar queries."""

from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared.models import BistSymbol, MarketBar

BIST_EXCHANGE = "BIST"
TR = ZoneInfo("Europe/Istanbul")
SESSION_OPEN = time(10, 0)
SESSION_CLOSE = time(18, 0)


def in_session(now: datetime | None = None) -> bool:
    """True if now is within BIST trading hours (TR time, Mon-Fri)."""
    now = now or datetime.now(TR)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC).astimezone(TR)
    else:
        now = now.astimezone(TR)
    if now.weekday() >= 5:
        return False
    return SESSION_OPEN <= now.time() < SESSION_CLOSE


async def active_bist_symbols(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        select(BistSymbol.symbol).where(BistSymbol.active.is_(True))
    )
    return sorted({r[0] for r in rows})


async def latest_bar(
    session: AsyncSession, symbol: str, *, interval: str
) -> MarketBar | None:
    stmt = (
        select(MarketBar)
        .where(MarketBar.symbol == symbol)
        .where(MarketBar.asset_class == "bist")
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
        .where(MarketBar.asset_class == "bist")
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

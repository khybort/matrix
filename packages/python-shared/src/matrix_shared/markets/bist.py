"""BIST (Borsa Istanbul) market adapter.

BIST is cash-equity, long-only, T+2 settled, trades 10:00–18:00 Europe/Istanbul
Mon–Fri. Universe lives in the `bist_symbols` table; for the sync
`claims_symbol` hot path we keep an optional in-process cache that startup
code refreshes via `refresh_known_symbols(db)`. Absent the cache, a regex
fallback (4-6 uppercase letters) handles tests and pre-warm boot.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import ClassVar
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared.models import BistSymbol, MarketBar

from .base import FeeModel, MarketAdapter
from .registry import register

TR = ZoneInfo("Europe/Istanbul")
SESSION_OPEN = time(10, 0)
SESSION_CLOSE = time(18, 0)

# Fallback heuristic: 4–6 uppercase letters, no digits, no quote suffix.
# Real BIST tickers (THYAO, GARAN, AKBNK, ASELS, ...) all fit this.
_BIST_SYMBOL_RE = re.compile(r"^[A-Z]{4,6}$")


class BistMarket(MarketAdapter):
    name: ClassVar[str] = "bist"
    asset_class: ClassVar[str] = "bist"

    # Cache of active BIST symbols. None = "not yet refreshed, use regex".
    _known_symbols: ClassVar[frozenset[str] | None] = None

    @classmethod
    def set_known_symbols(cls, symbols: set[str] | frozenset[str]) -> None:
        """Prime the in-process cache. Call after a successful universe refresh."""
        cls._known_symbols = frozenset(s.upper() for s in symbols)

    async def refresh_known_symbols(self, db: AsyncSession) -> None:
        rows = await db.execute(
            select(BistSymbol.symbol).where(BistSymbol.active.is_(True))
        )
        type(self).set_known_symbols({r[0] for r in rows})

    async def universe(self, db: AsyncSession) -> list[str]:
        rows = await db.execute(
            select(BistSymbol.symbol).where(BistSymbol.active.is_(True))
        )
        return sorted({r[0] for r in rows})

    def claims_symbol(self, symbol: str) -> bool:
        s = symbol.upper()
        cache = type(self)._known_symbols
        if cache is not None:
            return s in cache
        return bool(_BIST_SYMBOL_RE.match(s))

    def is_session_open(self, ts: datetime | None = None) -> bool:
        now = ts or datetime.now(TR)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC).astimezone(TR)
        else:
            now = now.astimezone(TR)
        if now.weekday() >= 5:
            return False
        return SESSION_OPEN <= now.time() < SESSION_CLOSE

    def fees(self, symbol: str) -> FeeModel:  # noqa: ARG002
        # Typical retail BIST broker: ~15 bps per side, 5 bps slippage budget.
        return FeeModel(
            maker_bps=Decimal("15"),
            taker_bps=Decimal("15"),
            slippage_bps=Decimal("5"),
        )

    def allows_short(self) -> bool:
        return False

    def settlement_days(self) -> int:
        return 2

    async def latest_price(self, db: AsyncSession, symbol: str) -> Decimal | None:
        stmt = (
            select(MarketBar.close)
            .where(MarketBar.symbol == symbol)
            .where(MarketBar.asset_class == "bist")
            .order_by(desc(MarketBar.ts))
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    def make_ingestor(self, cfg: object) -> "object":  # noqa: ARG002
        try:
            from ingestion.adapters.bist import BistIngestor
        except ImportError as e:
            raise RuntimeError(
                "bist ingestor requested but services/ingestion is not "
                "importable — install the ingestion service or run inside "
                "its container"
            ) from e
        return BistIngestor()

    def make_executor(self, cfg: object, *, paper: bool) -> "object":  # noqa: ARG002
        if paper:
            raise NotImplementedError(
                "bist paper executor is the services/backtest engine, not an "
                "ExecutionAdapter — wire via paper_trade.py instead"
            )
        try:
            from execution.adapters.bist import BistLiveExecutor
        except ImportError as e:
            raise RuntimeError(
                "bist live executor requested but services/execution is not "
                "importable — install the execution service or run inside its "
                "container"
            ) from e
        # Stub raises on call; instantiation is fine so the factory contract
        # works (callers see the wall on the first action, not on import).
        return BistLiveExecutor()


register(BistMarket())

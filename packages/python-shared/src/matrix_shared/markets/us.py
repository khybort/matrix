"""US equities market adapter (NYSE / Nasdaq cash equities).

Cash-equity, **short-allowed**, **T+1** settled, trades 09:30–16:00
America/New_York Mon–Fri (with the full US holiday calendar + 13:00 ET
early-close half-days — see `us_calendar`). Universe lives in the
`us_symbols` table; for the sync `claims_symbol` hot path we keep an
optional in-process cache that startup code refreshes via
`refresh_known_symbols(db)`.

Symbol routing note: US tickers (AAPL, MSFT) overlap BIST's 4–6 letter
regex fallback, so `claims_symbol` is **cache-only** — it returns False
until the universe cache is primed. This keeps `infer_market` unambiguous
(BIST vs US) at cold start; every persisted row already carries
`asset_class` explicitly, so nothing in the hot path depends on inferring
a US symbol before its universe has loaded.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared.models import MarketBar, UsSymbol

from . import us_calendar
from .base import FeeModel, MarketAdapter
from .registry import register


class UsMarket(MarketAdapter):
    name: ClassVar[str] = "us"
    asset_class: ClassVar[str] = "us"

    # Cache of active US symbols. None = "not yet refreshed" → claims nothing.
    _known_symbols: ClassVar[frozenset[str] | None] = None

    @classmethod
    def set_known_symbols(cls, symbols: set[str] | frozenset[str]) -> None:
        """Prime the in-process cache. Call after a successful universe refresh."""
        cls._known_symbols = frozenset(s.upper() for s in symbols)

    async def refresh_known_symbols(self, db: AsyncSession) -> None:
        rows = await db.execute(
            select(UsSymbol.symbol).where(UsSymbol.active.is_(True))
        )
        type(self).set_known_symbols({r[0] for r in rows})

    async def universe(self, db: AsyncSession) -> list[str]:
        rows = await db.execute(
            select(UsSymbol.symbol).where(UsSymbol.active.is_(True))
        )
        return sorted({r[0] for r in rows})

    def claims_symbol(self, symbol: str) -> bool:
        cache = type(self)._known_symbols
        if cache is None:
            return False
        return symbol.upper() in cache

    def is_session_open(self, ts: datetime | None = None) -> bool:
        return us_calendar.is_session_open(ts)

    def fees(self, symbol: str) -> FeeModel:  # noqa: ARG002
        # Commission-free retail (Alpaca/IBKR-lite). taker_bps models the
        # spread+SEC/TAF reg fees; slippage covers market-impact on 1m bars.
        return FeeModel(
            maker_bps=Decimal("0"),
            taker_bps=Decimal("1"),
            slippage_bps=Decimal("3"),
        )

    def allows_short(self) -> bool:
        return True

    def settlement_days(self) -> int:
        return 1  # T+1 since 2024-05-28

    async def latest_price(self, db: AsyncSession, symbol: str) -> Decimal | None:
        stmt = (
            select(MarketBar.close)
            .where(MarketBar.symbol == symbol)
            .where(MarketBar.asset_class == "us")
            .order_by(desc(MarketBar.ts))
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    def make_ingestor(self, cfg: object) -> "object":  # noqa: ARG002
        try:
            from ingestion.adapters.us import UsIngestor
        except ImportError as e:
            raise RuntimeError(
                "us ingestor requested but services/ingestion is not "
                "importable — install the ingestion service or run inside "
                "its container"
            ) from e
        return UsIngestor()

    def make_executor(self, cfg: object, *, paper: bool) -> "object":  # noqa: ARG002
        if paper:
            raise NotImplementedError(
                "us paper executor is the services/backtest engine, not an "
                "ExecutionAdapter — wire via paper_trade.py instead"
            )
        try:
            from execution.adapters.us import UsAlpacaExecutor
        except ImportError as e:
            raise RuntimeError(
                "us live executor requested but services/execution is not "
                "importable — install the execution service or run inside its "
                "container"
            ) from e
        return UsAlpacaExecutor()


register(UsMarket())

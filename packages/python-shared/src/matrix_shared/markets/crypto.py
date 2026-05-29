"""Crypto market adapter — Bybit/Binance-style perpetual & spot.

Universe is env-driven (`CRYPTO_SYMBOLS=BTCUSDT,ETHUSDT,...`) or dynamically
read from the `tradable_symbols` SHARED table (when populated by the universe
manager). Falls back to the hardcoded 15-symbol default when both are absent.
Live execution wiring lives in `services/execution/`; this module only declares
routing + rules.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared.models import MarketTrade

from .base import FeeModel, MarketAdapter
from .registry import register

# Suffixes that unambiguously mark a symbol as crypto-quoted.
_QUOTE_SUFFIXES: tuple[str, ...] = ("USDT", "USDC", "USD", "BUSD", "FDUSD")

# Fallback universe when CRYPTO_SYMBOLS env is unset and the DB table is empty.
_DEFAULT_UNIVERSE: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "TRXUSDT",
    "NEARUSDT",
    "APTUSDT",
    "ARBUSDT",
    "SUIUSDT",
)


async def _async_db_active_universe() -> list[str]:
    """Async read of active crypto symbols from SHARED tradable_symbols."""
    from matrix_shared import shared_session_scope
    async with shared_session_scope() as db:
        result = await db.execute(
            text(
                "SELECT symbol FROM tradable_symbols "
                "WHERE asset_class='crypto' AND active=true "
                "ORDER BY score DESC NULLS LAST"
            )
        )
        rows = [r[0] for r in result]
        return rows if rows else list(_DEFAULT_UNIVERSE)


def _db_active_universe() -> list[str]:
    """Sync bridge to the SHARED tradable_symbols active set.

    Uses asyncio.run() + asyncpg (always installed) so it works from any sync
    call site (ingestion startup, strategy modules, agent config loader) without
    needing psycopg2. Falls back to _DEFAULT_UNIVERSE on any error.
    """
    try:
        return asyncio.run(_async_db_active_universe())
    except Exception:
        return list(_DEFAULT_UNIVERSE)


def crypto_universe() -> list[str]:
    """Single source of truth for the crypto symbol set.

    Priority:
      1. CRYPTO_SYMBOLS env (explicit operator override — always respected).
      2. tradable_symbols SHARED table (dynamic universe from the potential-score
         manager). Falls back to _DEFAULT_UNIVERSE when the table is empty.

    Used by ingestion, every crypto strategy module, the agent, and
    CryptoMarket.universe() so the tradable set is defined in exactly one place.
    """
    env = os.environ.get("CRYPTO_SYMBOLS", "").strip()
    if env:
        return [s.strip().upper() for s in env.split(",") if s.strip()]
    return _db_active_universe()


class CryptoMarket(MarketAdapter):
    name: ClassVar[str] = "crypto"
    asset_class: ClassVar[str] = "crypto"

    async def universe(self, db: AsyncSession) -> list[str]:
        env = os.environ.get("CRYPTO_SYMBOLS", "").strip()
        if env:
            return [s.strip().upper() for s in env.split(",") if s.strip()]
        # Async path — preferred when a session is already available.
        try:
            result = await db.execute(
                text(
                    "SELECT symbol FROM tradable_symbols "
                    "WHERE asset_class='crypto' AND active=true "
                    "ORDER BY score DESC NULLS LAST"
                )
            )
            rows = [r[0] for r in result]
            return rows if rows else list(_DEFAULT_UNIVERSE)
        except Exception:
            return list(_DEFAULT_UNIVERSE)

    def claims_symbol(self, symbol: str) -> bool:
        s = symbol.upper()
        return any(s.endswith(suf) and len(s) > len(suf) for suf in _QUOTE_SUFFIXES)

    def is_session_open(self, ts: datetime | None = None) -> bool:  # noqa: ARG002
        return True  # 24/7

    def fees(self, symbol: str) -> FeeModel:  # noqa: ARG002
        return FeeModel(
            maker_bps=Decimal("1"),
            taker_bps=Decimal("10"),
            slippage_bps=Decimal("2"),
        )

    def allows_short(self) -> bool:
        return True

    def settlement_days(self) -> int:
        return 0

    async def latest_price(self, db: AsyncSession, symbol: str) -> Decimal | None:
        stmt = (
            select(MarketTrade.price)
            .where(MarketTrade.symbol == symbol)
            .order_by(desc(MarketTrade.trade_ts))
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    def make_ingestor(self, cfg: object) -> "object":  # noqa: ARG002
        try:
            from ingestion.adapters.crypto import CryptoIngestor
        except ImportError as e:
            raise RuntimeError(
                "crypto ingestor requested but services/ingestion is not "
                "importable — install the ingestion service or run inside "
                "its container"
            ) from e
        return CryptoIngestor(
            symbols=getattr(cfg, "symbols", None),
            testnet=getattr(cfg, "testnet", None),
        )

    def make_executor(self, cfg: object, *, paper: bool) -> "object":
        if paper:
            raise NotImplementedError(
                "crypto paper executor is the services/backtest engine, not an "
                "ExecutionAdapter — wire via paper_trade.py instead"
            )
        try:
            from execution.adapters.crypto import CryptoLiveExecutor
        except ImportError as e:
            raise RuntimeError(
                "crypto live executor requested but services/execution is not "
                "importable — install the execution service or run inside its "
                "container"
            ) from e
        testnet = getattr(cfg, "testnet", None)
        return CryptoLiveExecutor(testnet=testnet)


register(CryptoMarket())

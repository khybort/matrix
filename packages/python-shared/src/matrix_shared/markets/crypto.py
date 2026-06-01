"""Crypto market adapter — Bybit/Binance-style perpetual & spot.

Universe is discovered dynamically:
  1. `tradable_symbols` active set (universe manager — primary)
  2. `screener_universe_snapshot` top liquidity (bootstrap before first reconcile)
  3. `CRYPTO_SYMBOLS` env (operator override only)

No hardcoded symbol list. Empty universe until screener + labs have run.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from loguru import logger
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared.models import MarketTrade

from .base import FeeModel, MarketAdapter
from .registry import register

_QUOTE_SUFFIXES: tuple[str, ...] = ("USDT", "USDC", "USD", "BUSD", "FDUSD")
_BOOTSTRAP_LIMIT = int(os.environ.get("CRYPTO_UNIVERSE_BOOTSTRAP_N", "25"))


async def _async_bootstrap_from_screener(limit: int = _BOOTSTRAP_LIMIT) -> list[str]:
    """Cold-start: top-N Bybit USDT perps by 24h turnover from the screener snapshot."""
    from matrix_shared import session_scope

    try:
        async with session_scope() as db:
            res = await db.execute(
                text(
                    "SELECT symbol FROM screener_universe_snapshot "
                    "WHERE turnover24h IS NOT NULL AND turnover24h > 0 "
                    "ORDER BY turnover24h DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
            return [r[0] for r in res]
    except Exception as e:
        logger.debug(f"crypto universe screener bootstrap unavailable: {e}")
        return []


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
        if rows:
            return rows
    bootstrap = await _async_bootstrap_from_screener()
    if bootstrap:
        logger.info(
            f"crypto universe: tradable_symbols empty; bootstrapping "
            f"{len(bootstrap)} symbols from screener snapshot"
        )
        return bootstrap
    logger.warning(
        "crypto universe empty — enable screener + UNIVERSE_MANAGER_ENABLED "
        "or set CRYPTO_SYMBOLS for a manual override"
    )
    return []


def _db_active_universe() -> list[str]:
    """Sync bridge when no event loop is running (CLI, cold import).

    Inside a running loop, returns [] — callers must use crypto_universe_async().
    """
    try:
        asyncio.get_running_loop()
        return []
    except RuntimeError:
        pass
    try:
        return asyncio.run(_async_db_active_universe())
    except Exception:
        return []


def crypto_universe() -> list[str]:
    """Single source of truth for the crypto symbol set (sync)."""
    env = os.environ.get("CRYPTO_SYMBOLS", "").strip()
    if env:
        return [s.strip().upper() for s in env.split(",") if s.strip()]
    return _db_active_universe()


async def crypto_universe_async() -> list[str]:
    """Async single-source-of-truth — safe under a running event loop."""
    env = os.environ.get("CRYPTO_SYMBOLS", "").strip()
    if env:
        return [s.strip().upper() for s in env.split(",") if s.strip()]
    return await _async_db_active_universe()


class CryptoMarket(MarketAdapter):
    name: ClassVar[str] = "crypto"
    asset_class: ClassVar[str] = "crypto"

    async def universe(self, db: AsyncSession) -> list[str]:
        env = os.environ.get("CRYPTO_SYMBOLS", "").strip()
        if env:
            return [s.strip().upper() for s in env.split(",") if s.strip()]
        try:
            result = await db.execute(
                text(
                    "SELECT symbol FROM tradable_symbols "
                    "WHERE asset_class='crypto' AND active=true "
                    "ORDER BY score DESC NULLS LAST"
                )
            )
            rows = [r[0] for r in result]
            if rows:
                return rows
        except Exception:
            pass
        bootstrap = await _async_bootstrap_from_screener()
        return bootstrap if bootstrap else []

    def claims_symbol(self, symbol: str) -> bool:
        s = symbol.upper()
        return any(s.endswith(suf) and len(s) > len(suf) for suf in _QUOTE_SUFFIXES)

    def is_session_open(self, ts: datetime | None = None) -> bool:  # noqa: ARG002
        return True

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

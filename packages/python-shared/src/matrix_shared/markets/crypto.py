"""Crypto market adapter — Bybit/Binance-style perpetual & spot.

Universe is currently env-driven (`CRYPTO_SYMBOLS=BTCUSDT,ETHUSDT,...`)
with a small default list. Live execution wiring lives in
`services/execution/`; this module only declares routing + rules.
"""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix_shared.models import MarketTrade

from .base import FeeModel, MarketAdapter
from .registry import register

# Suffixes that unambiguously mark a symbol as crypto-quoted.
_QUOTE_SUFFIXES: tuple[str, ...] = ("USDT", "USDC", "USD", "BUSD", "FDUSD")

# Fallback universe when CRYPTO_SYMBOLS env is unset. The 15 most liquid
# Bybit USDT perpetuals — broad enough that mean-reversion / OI strategies
# have real alpha surface (the old 2-symbol BTC+ETH set was the single
# biggest reason "new coins aren't analyzed"), liquid enough that paper
# fills stay realistic. Override per-deployment via CRYPTO_SYMBOLS.
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


def crypto_universe() -> list[str]:
    """Single source of truth for the crypto symbol set.

    Reads CRYPTO_SYMBOLS (comma-separated) if set, else the default 15.
    Used by ingestion, every crypto strategy module, the agent, and
    CryptoMarket.universe() so the tradable set is defined in exactly one
    place — no more per-module DEFAULT_SYMBOLS drift.
    """
    env = os.environ.get("CRYPTO_SYMBOLS", "").strip()
    if env:
        return [s.strip().upper() for s in env.split(",") if s.strip()]
    return list(_DEFAULT_UNIVERSE)


class CryptoMarket(MarketAdapter):
    name: ClassVar[str] = "crypto"
    asset_class: ClassVar[str] = "crypto"

    async def universe(self, db: AsyncSession) -> list[str]:  # noqa: ARG002
        return crypto_universe()

    def claims_symbol(self, symbol: str) -> bool:
        s = symbol.upper()
        # Must end with a known quote suffix AND be longer than the suffix
        # itself (the bare "USDT" string isn't a tradable symbol).
        return any(s.endswith(suf) and len(s) > len(suf) for suf in _QUOTE_SUFFIXES)

    def is_session_open(self, ts: datetime | None = None) -> bool:  # noqa: ARG002
        return True  # 24/7

    def fees(self, symbol: str) -> FeeModel:  # noqa: ARG002
        # Bybit perpetual defaults (taker 10 bps, maker 1 bp, 2 bps slippage).
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
            # Paper PnL is the Wallet/PaperPosition engine in services/backtest;
            # there's no separate ExecutionAdapter for it. Strategies → paper
            # via the backtest loop, not via this factory.
            raise NotImplementedError(
                "crypto paper executor is the services/backtest engine, not an "
                "ExecutionAdapter — wire via paper_trade.py instead"
            )
        # Late import keeps matrix_shared free of a hard dep on services/.
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

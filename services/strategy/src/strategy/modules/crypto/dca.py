"""DCA v1 — pure long-only accumulator.

Hypothesis: long-bias on crypto majors with regular cadence beats most
short-horizon noise traders over multi-year horizons. Modeled here as a
flat-confidence LONG emitted every INTERVAL_MINUTES per symbol.

This is the simplest possible strategy and serves two purposes:
  - it gives the paper-trade engine a deterministic baseline to compare
    other strategies against.
  - it exercises the SHARED-tier dedup path (last-fired check) used by
    the grid module as well.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import session_scope, shared_session_scope
from matrix_shared.models import MarketTrade, Prediction

from matrix_shared.markets.crypto import crypto_universe

from strategy.base import PredictionDraft

STRATEGY_ID = "dca"
STRATEGY_VERSION = 1
HORIZON_S = 3600  # 1h
INTERVAL_MINUTES = 60
DEFAULT_SYMBOLS = tuple(crypto_universe())
CONFIDENCE = Decimal("0.30")


class Dca:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    horizon_seconds: int = HORIZON_S
    market: str = "crypto"

    def __init__(
        self,
        symbols: Sequence[str] = DEFAULT_SYMBOLS,
        interval_minutes: int = INTERVAL_MINUTES,
    ) -> None:
        # Empty list → use full universe (UI templates pass [] so the module
        # tracks the operator's CRYPTO_SYMBOLS env without hardcoded picks).
        self.symbols = list(symbols) if symbols else list(crypto_universe())
        self.interval_minutes = interval_minutes

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        drafts: list[PredictionDraft] = []

        async with session_scope() as local:
            for symbol in self.symbols:
                # Most recent trade — entry price reference + freshness gate
                px_stmt = (
                    select(MarketTrade.price, MarketTrade.exchange)
                    .where(MarketTrade.symbol == symbol)
                    .order_by(desc(MarketTrade.trade_ts))
                    .limit(1)
                )
                px_row = (await local.execute(px_stmt)).first()
                if px_row is None:
                    continue
                last_price = Decimal(px_row.price)
                exchange = px_row.exchange

                # Last DCA prediction for this symbol (SHARED tier)
                async with shared_session_scope() as shared:
                    last_stmt = (
                        select(Prediction.generated_at)
                        .where(Prediction.strategy_id == STRATEGY_ID)
                        .where(Prediction.symbol == symbol)
                        .order_by(desc(Prediction.generated_at))
                        .limit(1)
                    )
                    last_row = (await shared.execute(last_stmt)).first()
                if last_row is not None:
                    elapsed = now - last_row.generated_at
                    if elapsed < timedelta(minutes=self.interval_minutes):
                        continue

                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange=exchange,
                        side="long",
                        confidence=CONFIDENCE,
                        horizon_seconds=self.horizon_seconds,
                        entry_price_ref=last_price,
                        generated_at=now,
                        thesis=(
                            f"dca cadence={self.interval_minutes}m; "
                            f"long-only accumulator at px={last_price}"
                        ),
                        context={
                            "interval_minutes": self.interval_minutes,
                            "confidence": str(CONFIDENCE),
                        },
                    )
                )
                logger.info(
                    f"{self.id}: signal {symbol} long conf={CONFIDENCE} px={last_price}"
                )

        return drafts

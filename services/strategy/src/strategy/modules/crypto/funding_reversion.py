"""Funding-rate mean-reversion v1.

Hypothesis: when funding rate is extreme (longs paying shorts heavily, or
vice versa), the crowded side often unwinds. Trade the fade.

Implementation:
    Read latest TickerSnapshot for each symbol.
    If funding_rate > +HIGH_FUNDING (e.g. > +0.02%/8h): bias SHORT (fade longs)
    If funding_rate < -HIGH_FUNDING:                    bias LONG (fade shorts)
    Confidence scales linearly with |funding| up to FUNDING_CAP.

Horizon is several hours by default since funding is a slow-moving signal.
For smoketest we keep it short.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import session_scope
from matrix_shared.models import MarketTrade, TickerSnapshot

from matrix_shared.markets.crypto import crypto_universe

from strategy.base import PredictionDraft

STRATEGY_ID = "funding_reversion"
STRATEGY_VERSION = 1
HIGH_FUNDING = Decimal("0.0002")  # ±0.02% (per 8h) threshold to act
FUNDING_CAP = Decimal("0.0005")  # ±0.05% maps to confidence 1.0
HORIZON_S = 600  # 10min outcome window — funding effects slower than trade flow
DEFAULT_SYMBOLS = tuple(crypto_universe())


class FundingReversion:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "crypto"

    def __init__(
        self,
        symbols: Sequence[str] = DEFAULT_SYMBOLS,
        *,
        high_funding: Decimal = HIGH_FUNDING,
        funding_cap: Decimal = FUNDING_CAP,
        horizon_s: int = HORIZON_S,
    ) -> None:
        self.symbols = list(symbols)
        self.high_funding = high_funding
        self.funding_cap = funding_cap
        self.horizon_seconds = horizon_s

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        drafts: list[PredictionDraft] = []

        async with session_scope() as session:
            for symbol in self.symbols:
                # Latest ticker (must have funding_rate)
                tk_stmt = (
                    select(TickerSnapshot.funding_rate, TickerSnapshot.exchange)
                    .where(TickerSnapshot.symbol == symbol)
                    .where(TickerSnapshot.funding_rate.isnot(None))
                    .order_by(desc(TickerSnapshot.snapshot_ts))
                    .limit(1)
                )
                tk_row = (await session.execute(tk_stmt)).first()
                if tk_row is None:
                    continue
                fr = Decimal(tk_row.funding_rate)
                if abs(fr) < self.high_funding:
                    continue

                # Latest price for entry reference
                px_stmt = (
                    select(MarketTrade.price)
                    .where(MarketTrade.symbol == symbol)
                    .order_by(desc(MarketTrade.trade_ts))
                    .limit(1)
                )
                px_row = (await session.execute(px_stmt)).first()
                if px_row is None:
                    continue
                last_px = Decimal(px_row.price)

                side = "short" if fr > 0 else "long"
                # confidence in [0, 1]
                magnitude = min(abs(fr) / self.funding_cap, Decimal("1"))
                confidence = max(Decimal("0.05"), magnitude)

                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange=tk_row.exchange,
                        side=side,
                        confidence=confidence,
                        horizon_seconds=self.horizon_seconds,
                        entry_price_ref=last_px,
                        generated_at=now,
                        thesis=(
                            f"funding_rate={fr*100:.4f}% — extreme {('positive' if fr > 0 else 'negative')}; "
                            f"fade with {side}"
                        ),
                        context={
                            "funding_rate": str(fr),
                            "high_funding_threshold": str(self.high_funding),
                            "magnitude_ratio": str(magnitude),
                        },
                    )
                )
                logger.info(
                    f"{self.id}: signal {symbol} {side} conf={confidence:.3f} "
                    f"funding={fr*100:.4f}%"
                )

        return drafts

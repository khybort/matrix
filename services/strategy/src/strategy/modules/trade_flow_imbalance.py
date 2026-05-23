"""Trade-flow imbalance v1.

Hypothesis: a sharp imbalance between buy-side and sell-side trade volume
in a short window is a noisy mean-reversion signal at the next-minute scale.

Implementation:
    For each tracked symbol, look at trades in the last LOOKBACK_S seconds.
    Compute buy_share = buy_vol / (buy_vol + sell_vol).
    If buy_share > BUY_THRESHOLD → SHORT signal (we expect reversion)
    If buy_share < SELL_THRESHOLD → LONG signal
    Otherwise → no signal.

This is intentionally simple and is expected to be marginal-at-best.
Its real job is to give the self-improvement loop something to score and tune.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select

from matrix_shared import session_scope
from matrix_shared.models import MarketTrade

from strategy.base import PredictionDraft

STRATEGY_ID = "trade_flow_imbalance"
STRATEGY_VERSION = 1
LOOKBACK_S = 60
HORIZON_S = 90  # short-horizon mean reversion
BUY_THRESHOLD = Decimal("0.65")  # >65% buy → short
SELL_THRESHOLD = Decimal("0.35")  # <35% buy → long
MIN_TRADES = 5
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")


class TradeFlowImbalance:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    horizon_seconds: int = HORIZON_S

    def __init__(self, symbols: Sequence[str] = DEFAULT_SYMBOLS) -> None:
        self.symbols = list(symbols)

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        since = now - timedelta(seconds=LOOKBACK_S)
        drafts: list[PredictionDraft] = []
        async with session_scope() as session:
            for symbol in self.symbols:
                stmt = (
                    select(
                        MarketTrade.side,
                        MarketTrade.size,
                        MarketTrade.price,
                        MarketTrade.exchange,
                    )
                    .where(MarketTrade.symbol == symbol)
                    .where(MarketTrade.trade_ts >= since)
                    .order_by(MarketTrade.trade_ts.asc())
                )
                rows = (await session.execute(stmt)).all()
                if len(rows) < MIN_TRADES:
                    continue

                buy_vol = sum((r.size for r in rows if r.side == "buy"), start=Decimal(0))
                sell_vol = sum((r.size for r in rows if r.side == "sell"), start=Decimal(0))
                total_vol = buy_vol + sell_vol
                if total_vol == 0:
                    continue

                buy_share = buy_vol / total_vol
                last_price = rows[-1].price

                side: str | None = None
                confidence: Decimal = Decimal("0")
                if buy_share >= BUY_THRESHOLD:
                    side = "short"
                    confidence = (buy_share - BUY_THRESHOLD) / (Decimal("1") - BUY_THRESHOLD)
                elif buy_share <= SELL_THRESHOLD:
                    side = "long"
                    confidence = (SELL_THRESHOLD - buy_share) / SELL_THRESHOLD
                if side is None:
                    continue

                confidence = max(Decimal("0.01"), min(Decimal("0.99"), confidence))
                last_exchange = rows[-1].exchange

                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange=last_exchange,
                        side=side,
                        confidence=confidence,
                        horizon_seconds=self.horizon_seconds,
                        entry_price_ref=last_price,
                        generated_at=now,
                        thesis=(
                            f"buy_share={buy_share:.3f} in {LOOKBACK_S}s; "
                            f"n_trades={len(rows)}; mean-reversion {side}"
                        ),
                        context={
                            "lookback_s": LOOKBACK_S,
                            "n_trades": len(rows),
                            "buy_vol": str(buy_vol),
                            "sell_vol": str(sell_vol),
                            "buy_share": str(buy_share),
                        },
                    )
                )
                logger.info(
                    f"{self.id}: signal {symbol} {side} conf={confidence:.3f} "
                    f"buy_share={buy_share:.3f} n={len(rows)}"
                )
        return drafts

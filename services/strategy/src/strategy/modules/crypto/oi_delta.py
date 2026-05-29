"""Open-interest delta momentum v1.

Hypothesis: a meaningful OI surge with a same-direction price move is a
short-horizon momentum signal — fresh positions piling on confirm the trend.
A surge with conflicting price action is ambiguous, so we require both.

Implementation:
    For each symbol:
      now_ticker, prev_ticker (≈5min ago) → oi_delta_pct, price_delta_pct
      If oi_delta_pct > +OI_JUMP and price_delta_pct > +PRICE_CONFIRM → LONG
      If oi_delta_pct > +OI_JUMP and price_delta_pct < -PRICE_CONFIRM → SHORT
      Otherwise skip.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import session_scope
from matrix_shared.models import MarketTrade, TickerSnapshot

from matrix_shared.markets.crypto import crypto_universe

from strategy.base import PredictionDraft

STRATEGY_ID = "oi_delta"
STRATEGY_VERSION = 1
LOOKBACK_S = 300  # 5min
OI_JUMP_THRESHOLD = Decimal("0.015")  # 1.5% OI surge
PRICE_CONFIRM_THRESHOLD = Decimal("0.0015")  # 0.15% same-direction price move
HORIZON_S = 300
DEFAULT_SYMBOLS = tuple(crypto_universe())
DEFAULT_TP_PCT = Decimal("0.015")  # 1.5% take-profit
DEFAULT_SL_PCT = Decimal("0.0075")  # 0.75% stop-loss


class OiDelta:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    horizon_seconds: int = HORIZON_S
    market: str = "crypto"

    def __init__(
        self,
        symbols: Sequence[str] = DEFAULT_SYMBOLS,
        *,
        tp_pct: Decimal | None = DEFAULT_TP_PCT,
        sl_pct: Decimal | None = DEFAULT_SL_PCT,
    ) -> None:
        self.symbols = list(symbols)
        self.tp_pct = Decimal(str(tp_pct)) if tp_pct is not None else None
        self.sl_pct = Decimal(str(sl_pct)) if sl_pct is not None else None

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=LOOKBACK_S)
        drafts: list[PredictionDraft] = []

        async with session_scope() as session:
            for symbol in self.symbols:
                # Latest ticker
                cur_stmt = (
                    select(TickerSnapshot)
                    .where(TickerSnapshot.symbol == symbol)
                    .where(TickerSnapshot.open_interest.isnot(None))
                    .order_by(desc(TickerSnapshot.snapshot_ts))
                    .limit(1)
                )
                cur = (await session.execute(cur_stmt)).scalar_one_or_none()
                if cur is None or cur.open_interest is None:
                    continue

                # Ticker from ≈5min ago
                prev_stmt = (
                    select(TickerSnapshot)
                    .where(TickerSnapshot.symbol == symbol)
                    .where(TickerSnapshot.snapshot_ts <= cutoff)
                    .where(TickerSnapshot.open_interest.isnot(None))
                    .order_by(desc(TickerSnapshot.snapshot_ts))
                    .limit(1)
                )
                prev = (await session.execute(prev_stmt)).scalar_one_or_none()
                if prev is None or prev.open_interest is None or prev.open_interest == 0:
                    continue

                oi_delta = (cur.open_interest - prev.open_interest) / prev.open_interest
                if oi_delta < OI_JUMP_THRESHOLD:
                    continue

                # Price delta same window
                price_now_stmt = (
                    select(MarketTrade.price)
                    .where(MarketTrade.symbol == symbol)
                    .order_by(desc(MarketTrade.trade_ts))
                    .limit(1)
                )
                px_now_row = (await session.execute(price_now_stmt)).first()
                if px_now_row is None:
                    continue
                px_now = Decimal(px_now_row.price)

                price_then_stmt = (
                    select(MarketTrade.price)
                    .where(MarketTrade.symbol == symbol)
                    .where(MarketTrade.trade_ts <= cutoff)
                    .order_by(desc(MarketTrade.trade_ts))
                    .limit(1)
                )
                px_then_row = (await session.execute(price_then_stmt)).first()
                if px_then_row is None:
                    continue
                px_then = Decimal(px_then_row.price)
                if px_then == 0:
                    continue
                price_delta = (px_now - px_then) / px_then

                if price_delta > PRICE_CONFIRM_THRESHOLD:
                    side = "long"
                elif price_delta < -PRICE_CONFIRM_THRESHOLD:
                    side = "short"
                else:
                    continue

                confidence = min(Decimal("1"), oi_delta / Decimal("0.05"))  # 5% OI → conf 1.0
                confidence = max(Decimal("0.1"), confidence)

                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange=cur.exchange,
                        side=side,
                        confidence=confidence,
                        horizon_seconds=self.horizon_seconds,
                        entry_price_ref=px_now,
                        generated_at=now,
                        thesis=(
                            f"oi_jump={oi_delta*100:.2f}% over {LOOKBACK_S}s + "
                            f"price_delta={price_delta*100:.3f}% confirming {side}"
                        ),
                        context={
                            "oi_delta_pct": str(oi_delta),
                            "price_delta_pct": str(price_delta),
                            "lookback_s": LOOKBACK_S,
                        },
                        tp_pct=self.tp_pct,
                        sl_pct=self.sl_pct,
                    )
                )
                logger.info(
                    f"{self.id}: signal {symbol} {side} conf={confidence:.3f} "
                    f"oi_delta={oi_delta*100:.2f}% price_delta={price_delta*100:.3f}%"
                )

        return drafts

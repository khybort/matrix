"""Pure OI breakout — long-horizon momentum v1.

Hypothesis: from the matrix_agent alpha-diagnostic (n=441 trades), the
oi_delta feature was the ONLY signal among five with positive expectancy
when given a 1800s horizon — 42% win rate vs ~5% for the rule-blender as
a whole. The existing `oi_delta` strategy gates on co-directional price
confirmation, which probably filters out the early portion of a true
breakout (price hasn't moved yet). This module isolates the signal:
fire on a big OI surge at the 5-minute lookback, hold for 30 minutes.

Rationale for not requiring price confirmation: a large OI surge with
flat price IS the early phase of accumulation; waiting for price
confirmation captures only the late phase, where slippage erodes edge.

Confidence caps at 0.5 (conservative — the diagnostic was 42% win, not
60%; we don't want to over-bet on a hypothesis until live evidence
accumulates).

Crypto-only; honors agent_lessons via the agent layer if wired.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import session_scope
from matrix_shared.models import MarketTrade, TickerSnapshot

from strategy.base import PredictionDraft

STRATEGY_ID = "oi_breakout"
STRATEGY_VERSION = 1
LOOKBACK_S = 300  # 5-min window for the OI delta measurement
OI_SURGE_THRESHOLD = Decimal("0.020")  # require >2% OI jump (stricter than v1)
OI_CAP = Decimal("0.05")  # 5% surge → confidence ceiling
CONFIDENCE_CEILING = Decimal("0.50")  # cap until evidence supports more
PRICE_DRIFT_TIE_BREAKER = Decimal("0.0005")  # 0.05% — direction nudge only
HORIZON_S = 1800  # 30-min hold matches the diagnostic's positive bucket
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")


class OiBreakout:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    horizon_seconds: int = HORIZON_S
    market: str = "crypto"
    asset_class: str = "crypto"

    def __init__(self, symbols: Sequence[str] = DEFAULT_SYMBOLS) -> None:
        self.symbols = list(symbols)

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=LOOKBACK_S)
        drafts: list[PredictionDraft] = []

        async with session_scope() as session:
            for symbol in self.symbols:
                cur = (await session.execute(
                    select(TickerSnapshot)
                    .where(TickerSnapshot.symbol == symbol)
                    .where(TickerSnapshot.open_interest.isnot(None))
                    .order_by(desc(TickerSnapshot.snapshot_ts))
                    .limit(1)
                )).scalar_one_or_none()
                if cur is None or cur.open_interest is None:
                    continue

                prev = (await session.execute(
                    select(TickerSnapshot)
                    .where(TickerSnapshot.symbol == symbol)
                    .where(TickerSnapshot.snapshot_ts <= cutoff)
                    .where(TickerSnapshot.open_interest.isnot(None))
                    .order_by(desc(TickerSnapshot.snapshot_ts))
                    .limit(1)
                )).scalar_one_or_none()
                if prev is None or not prev.open_interest:
                    continue

                oi_delta = (cur.open_interest - prev.open_interest) / prev.open_interest
                if abs(oi_delta) < OI_SURGE_THRESHOLD:
                    continue

                # Latest price for entry reference.
                px_row = (await session.execute(
                    select(MarketTrade.price)
                    .where(MarketTrade.symbol == symbol)
                    .order_by(desc(MarketTrade.trade_ts))
                    .limit(1)
                )).first()
                if px_row is None:
                    continue
                px_now = Decimal(px_row.price)

                # Tie-break direction from a small same-window price drift.
                # We DON'T require price confirmation (the whole point of
                # this module), but a small drift breaks the long/short tie.
                drift_row = (await session.execute(
                    select(MarketTrade.price)
                    .where(MarketTrade.symbol == symbol)
                    .where(MarketTrade.trade_ts <= cutoff)
                    .order_by(desc(MarketTrade.trade_ts))
                    .limit(1)
                )).first()
                price_drift = (
                    (px_now - Decimal(drift_row.price)) / Decimal(drift_row.price)
                    if drift_row and Decimal(drift_row.price) > 0
                    else Decimal("0")
                )

                # OI rising + price up OR flat → long (accumulation)
                # OI rising + price down → short (capitulation distribution)
                # OI falling → could be capitulation; we don't trade that yet.
                if oi_delta <= 0:
                    continue
                side = "long" if price_drift >= -PRICE_DRIFT_TIE_BREAKER else "short"

                magnitude = min(Decimal("1"), abs(oi_delta) / OI_CAP)
                confidence = min(CONFIDENCE_CEILING, max(Decimal("0.1"), magnitude * CONFIDENCE_CEILING))

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
                            f"oi_surge={oi_delta * 100:.2f}% / {LOOKBACK_S}s, "
                            f"price_drift={price_drift * 100:.3f}% → {side} (conf capped {CONFIDENCE_CEILING})"
                        ),
                        context={
                            "oi_delta_pct": str(oi_delta),
                            "price_drift_pct": str(price_drift),
                            "lookback_s": LOOKBACK_S,
                            "horizon_s": HORIZON_S,
                            "confidence_ceiling": str(CONFIDENCE_CEILING),
                        },
                    )
                )
                logger.info(
                    f"{self.id}: {symbol} {side} oi_delta={oi_delta * 100:.2f}% "
                    f"drift={price_drift * 100:.3f}% conf={confidence:.3f}"
                )
        return drafts

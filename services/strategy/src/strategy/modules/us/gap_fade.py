"""US opening-gap fade v1 (long + short).

Hypothesis: US equities open with overnight gaps that frequently mean-revert
in the first hour. Large gap up → fade short; large gap down → fade long.
Unlike BIST, US allows shorting, so both directions are actionable.

Read prior 1d close vs the latest 1m bar (open-of-day proxy). If
|gap| >= threshold, emit a fade in the opposite direction, confidence scaling
with gap magnitude. Dedup against open predictions within one horizon so the
static opening gap isn't re-emitted every tick.

Horizon: 30min (intraday mean reversion).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select

from matrix_shared import session_scope, shared_session_scope
from matrix_shared.models import Prediction

from strategy.base import PredictionDraft
from strategy.modules.us._helpers import (
    US_EXCHANGE,
    active_us_symbols,
    in_session,
    latest_bar,
    recent_bars,
    safe_pct,
)

STRATEGY_ID = "us_gap_fade"
STRATEGY_VERSION = 1
GAP_THRESHOLD = Decimal("0.015")  # 1.5%
GAP_CAP = Decimal("0.05")  # 5% gap → confidence 1.0
HORIZON_S = 1800  # 30min
DEFAULT_TP_PCT = Decimal("0.020")  # 2% take-profit
DEFAULT_SL_PCT = Decimal("0.010")  # 1% stop-loss
DEDUP_WINDOW_S = HORIZON_S


class UsGapFade:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "us"
    asset_class: str = "us"
    horizon_seconds: int = HORIZON_S

    def __init__(
        self,
        *,
        gap_threshold: Decimal = GAP_THRESHOLD,
        gap_cap: Decimal = GAP_CAP,
        horizon_s: int = HORIZON_S,
        tp_pct: Decimal | None = DEFAULT_TP_PCT,
        sl_pct: Decimal | None = DEFAULT_SL_PCT,
    ) -> None:
        self.gap_threshold = gap_threshold
        self.gap_cap = gap_cap
        self.horizon_seconds = horizon_s
        self.dedup_window_s = horizon_s
        self.tp_pct = Decimal(str(tp_pct)) if tp_pct is not None else None
        self.sl_pct = Decimal(str(sl_pct)) if sl_pct is not None else None

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        if not in_session(now):
            return []
        drafts: list[PredictionDraft] = []

        async with session_scope() as session:
            symbols = await active_us_symbols(session)
            for symbol in symbols:
                eod_bars = await recent_bars(session, symbol, interval="1d", n=2)
                if len(eod_bars) < 1:
                    continue
                prior_close = Decimal(eod_bars[-1].close)

                intraday = await latest_bar(session, symbol, interval="1m")
                if intraday is None:
                    continue
                last_px = Decimal(intraday.close)

                gap = safe_pct(last_px - prior_close, prior_close)
                if abs(gap) < self.gap_threshold:
                    continue

                magnitude = min(abs(gap) / self.gap_cap, Decimal("1"))
                confidence = max(Decimal("0.05"), magnitude)
                # Fade the gap: gap down → long bounce; gap up → short fade.
                side = "long" if gap < 0 else "short"

                dedup_since = now - timedelta(seconds=self.dedup_window_s)
                async with shared_session_scope() as shared:
                    dup = (
                        await shared.execute(
                            select(Prediction.id)
                            .where(Prediction.strategy_id == STRATEGY_ID)
                            .where(Prediction.strategy_version == self.version)
                            .where(Prediction.symbol == symbol)
                            .where(Prediction.side == side)
                            .where(Prediction.generated_at >= dedup_since)
                            .limit(1)
                        )
                    ).first()
                if dup is not None:
                    continue

                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange=US_EXCHANGE,
                        asset_class=self.asset_class,
                        side=side,
                        confidence=confidence,
                        horizon_seconds=self.horizon_seconds,
                        entry_price_ref=last_px,
                        generated_at=now,
                        thesis=(
                            f"gap {gap * 100:.2f}% from prior close {prior_close} → "
                            f"{side} fade"
                        ),
                        context={
                            "prior_close": str(prior_close),
                            "last_px": str(last_px),
                            "gap_pct": str(gap),
                        },
                        tp_pct=self.tp_pct,
                        sl_pct=self.sl_pct,
                    )
                )
                logger.info(
                    f"{self.id}: {symbol} gap={gap * 100:.2f}% side={side} conf={confidence:.3f}"
                )

        return drafts

"""BIST opening-gap fade v1.

Hypothesis: BIST equities open with overnight gaps that frequently mean-revert
within the first session. Large gap up → fade short; large gap down → fade
long. Long-only constraint (default Turkish equity market rule) means we
*only* emit `long` signals on gap-downs and `flat`/no-signal on gap-ups, but
we record the gap-up case in `context` so the dashboard can see what the
strategy *would* have done.

Implementation:
    Read last 1d bar close vs. latest 1m bar (open-of-day proxy in session;
    latest close out of session) for each active BIST symbol.
    If gap_pct < -GAP_THRESHOLD → emit long with confidence ~ |gap|.
    If gap_pct > +GAP_THRESHOLD → record short hypothesis (paper only, not
    actionable under long-only rule).

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
from strategy.modules.bist._helpers import (
    BIST_EXCHANGE,
    active_bist_symbols,
    in_session,
    latest_bar,
    recent_bars,
    safe_pct,
)

STRATEGY_ID = "bist_gap_fade"
STRATEGY_VERSION = 1
GAP_THRESHOLD = Decimal("0.015")  # 1.5%
GAP_CAP = Decimal("0.05")  # 5% gap → confidence 1.0
HORIZON_S = 1800  # 30min
# The opening gap is a once-per-day, slow-moving signal — its value barely
# moves intraday. Without dedup the 30s generator loop re-emits the same
# ~28 signals every tick (~960 rows/symbol/session). Suppress re-emission
# within one horizon window: at most one open prediction per symbol+side
# at a time, re-evaluated after the outcome window closes.
DEDUP_WINDOW_S = HORIZON_S


class BistGapFade:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "bist"
    asset_class: str = "bist"

    def __init__(
        self,
        *,
        gap_threshold: Decimal = GAP_THRESHOLD,
        gap_cap: Decimal = GAP_CAP,
        horizon_s: int = HORIZON_S,
    ) -> None:
        self.gap_threshold = gap_threshold
        self.gap_cap = gap_cap
        self.horizon_seconds = horizon_s
        self.dedup_window_s = horizon_s

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        if not in_session(now):
            return []
        drafts: list[PredictionDraft] = []

        async with session_scope() as session:
            symbols = await active_bist_symbols(session)
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

                # Long-only: only act on gap-down (expect bounce). Gap-up is logged
                # but emitted as `flat` so the agent never tries to short BIST.
                side = "long" if gap < 0 else "flat"

                # Dedup against SHARED predictions within dedup_window_s so the
                # static opening gap isn't re-written every 30s tick.
                dedup_since = now - timedelta(seconds=self.dedup_window_s)
                async with shared_session_scope() as shared:
                    dup = (
                        await shared.execute(
                            select(Prediction.id)
                            .where(Prediction.strategy_id == STRATEGY_ID)
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
                        exchange=BIST_EXCHANGE,
                        asset_class=self.asset_class,
                        side=side,
                        confidence=confidence,
                        horizon_seconds=self.horizon_seconds,
                        entry_price_ref=last_px,
                        generated_at=now,
                        thesis=(
                            f"gap {gap * 100:.2f}% from prior close {prior_close} → "
                            f"{'long fade' if side == 'long' else 'observed (long-only, skip)'}"
                        ),
                        context={
                            "prior_close": str(prior_close),
                            "last_px": str(last_px),
                            "gap_pct": str(gap),
                            "long_only": True,
                        },
                    )
                )
                logger.info(
                    f"{self.id}: {symbol} gap={gap * 100:.2f}% side={side} conf={confidence:.3f}"
                )

        return drafts

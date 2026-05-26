"""BIST intraday reversion v1.

Hypothesis: bist_gap_fade (+41.7% win, the only profitable strategy 24h)
shows that mean-reversion long-fades work on BIST equities for opening
gaps. This module extends the same logic to intraday drawdowns: a symbol
that has dropped sharply from its session-open within the first 4 hours
often mean-reverts within the session.

Trigger: intraday drop from session-open > 3% AND not yet in the last
hour of the session (need room for reversion to play out).

Long-only (BIST T+2 settlement rule); confidence scales with drop
magnitude up to a 7% cap.

Horizon: 60 minutes (intraday play; modest hold to allow reversion
without overlapping the close-of-day spike).

Independence from gap_fade: this fires on intraday weakness measured
against the 09:30 first-print, not the previous-day close. A symbol
that opens flat and drops 4% in two hours triggers this but not
gap_fade. Vice versa, a gap-down open that immediately stabilizes
triggers gap_fade only.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select

from matrix_shared import session_scope
from matrix_shared.models import MarketBar

from strategy.base import PredictionDraft
from strategy.modules.bist._helpers import (
    BIST_EXCHANGE,
    TR,
    active_bist_symbols,
    in_session,
    latest_bar,
    safe_pct,
)

STRATEGY_ID = "bist_intraday_reversion"
STRATEGY_VERSION = 1
DROP_THRESHOLD = Decimal("0.03")  # require >3% intraday drop to act
DROP_CAP = Decimal("0.07")  # 7% drop → confidence 1.0
HORIZON_S = 3600  # 60-min hold for the reversion play
# Don't fire after this clock time — not enough session left for the
# reversion to complete.
LAST_ENTRY_HOUR_TR = time(17, 0)
# Session-open reference: first BIST 1m bar of the day (~10:00 TR).
_OPEN_LOOKBACK_HOURS = 12  # generous window — covers premarket gaps too


def _today_tr_open_utc(now_utc: datetime) -> datetime:
    """Compute today's 10:00 TR (session open) in UTC."""
    now_tr = now_utc.astimezone(TR)
    open_tr = now_tr.replace(hour=10, minute=0, second=0, microsecond=0)
    return open_tr.astimezone(UTC)


class BistIntradayReversion:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    horizon_seconds: int = HORIZON_S
    market: str = "bist"
    asset_class: str = "bist"

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        if not in_session(now):
            return []
        # Cut-off: stop opening new positions past 17:00 TR so a 60-min
        # reversion attempt finishes inside the session.
        now_tr = now.astimezone(TR).time()
        if now_tr >= LAST_ENTRY_HOUR_TR:
            return []

        session_open_utc = _today_tr_open_utc(now)
        # Just-in-case: if we're early in the session and the open bar
        # isn't there yet, fall back to the prior-N-hours window.
        early_cutoff = now - timedelta(hours=_OPEN_LOOKBACK_HOURS)
        ref_cutoff = max(session_open_utc, early_cutoff) if session_open_utc <= now else early_cutoff

        drafts: list[PredictionDraft] = []

        async with session_scope() as ses:
            symbols = await active_bist_symbols(ses)
            for symbol in symbols:
                # 1m bar at/just-after session open. We look for the
                # earliest 1m bar of today's session.
                open_bar = (await ses.execute(
                    select(MarketBar)
                    .where(MarketBar.symbol == symbol)
                    .where(MarketBar.asset_class == "bist")
                    .where(MarketBar.interval == "1m")
                    .where(MarketBar.ts >= ref_cutoff)
                    .order_by(MarketBar.ts.asc())
                    .limit(1)
                )).scalar_one_or_none()
                if open_bar is None:
                    continue
                open_px = Decimal(open_bar.open or open_bar.close)
                if open_px <= 0:
                    continue

                # Current 1m latest bar.
                cur_bar = await latest_bar(ses, symbol, interval="1m")
                if cur_bar is None:
                    continue
                last_px = Decimal(cur_bar.close)

                drop = safe_pct(last_px - open_px, open_px)
                if drop > -DROP_THRESHOLD:
                    # Not enough drawdown to call this an intraday weak hand.
                    continue

                magnitude = min(abs(drop) / DROP_CAP, Decimal("1"))
                confidence = max(Decimal("0.1"), magnitude)

                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange=BIST_EXCHANGE,
                        asset_class=self.asset_class,
                        side="long",
                        confidence=confidence,
                        horizon_seconds=self.horizon_seconds,
                        entry_price_ref=last_px,
                        generated_at=now,
                        thesis=(
                            f"intraday drop {drop * 100:.2f}% from session open "
                            f"{open_px} → long fade ({confidence:.3f})"
                        ),
                        context={
                            "session_open_px": str(open_px),
                            "last_px": str(last_px),
                            "drop_pct": str(drop),
                            "long_only": True,
                        },
                    )
                )
                logger.info(
                    f"{self.id}: {symbol} drop={drop * 100:.2f}% long conf={confidence:.3f}"
                )
        return drafts

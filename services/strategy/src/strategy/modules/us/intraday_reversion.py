"""US intraday reversion v1 (long + short).

Extends gap_fade's mean-reversion thesis to intraday moves measured from the
session open (09:30 ET first print). A symbol that has dropped sharply from
its open often bounces; a symbol that has spiked sharply often fades. US short
support means both are tradable.

Trigger: |move from session open| > threshold AND not in the last 45min of
the session (need room for reversion). Confidence scales with move magnitude
up to a 7% cap. Horizon: 60 minutes.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select

from matrix_shared import session_scope
from matrix_shared.markets.us_calendar import NY
from matrix_shared.models import MarketBar

from strategy.base import PredictionDraft
from strategy.modules.us._helpers import (
    ASSET_CLASS,
    US_EXCHANGE,
    active_us_symbols,
    in_session,
    latest_bar,
    safe_pct,
    session_open_utc,
)

STRATEGY_ID = "us_intraday_reversion"
STRATEGY_VERSION = 1
MOVE_THRESHOLD = Decimal("0.03")  # require >3% intraday move to act
MOVE_CAP = Decimal("0.07")  # 7% move → confidence 1.0
HORIZON_S = 3600  # 60-min hold
DEFAULT_TP_PCT = Decimal("0.025")
DEFAULT_SL_PCT = Decimal("0.015")
# Don't open after 15:15 ET — not enough session left for reversion.
LAST_ENTRY_TIME_NY = time(15, 15)
_OPEN_LOOKBACK_HOURS = 8


class UsIntradayReversion:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "us"
    asset_class: str = "us"
    horizon_seconds: int = HORIZON_S

    def __init__(
        self,
        *,
        move_threshold: Decimal = MOVE_THRESHOLD,
        move_cap: Decimal = MOVE_CAP,
        horizon_s: int = HORIZON_S,
        tp_pct: Decimal | None = DEFAULT_TP_PCT,
        sl_pct: Decimal | None = DEFAULT_SL_PCT,
    ) -> None:
        self.move_threshold = move_threshold
        self.move_cap = move_cap
        self.horizon_seconds = horizon_s
        self.tp_pct = Decimal(str(tp_pct)) if tp_pct is not None else None
        self.sl_pct = Decimal(str(sl_pct)) if sl_pct is not None else None

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        if not in_session(now):
            return []
        if now.astimezone(NY).time() >= LAST_ENTRY_TIME_NY:
            return []

        open_utc = session_open_utc(now)
        early_cutoff = now - timedelta(hours=_OPEN_LOOKBACK_HOURS)
        ref_cutoff = max(open_utc, early_cutoff) if open_utc <= now else early_cutoff

        drafts: list[PredictionDraft] = []
        async with session_scope() as ses:
            symbols = await active_us_symbols(ses)
            for symbol in symbols:
                open_bar = (
                    await ses.execute(
                        select(MarketBar)
                        .where(MarketBar.symbol == symbol)
                        .where(MarketBar.asset_class == ASSET_CLASS)
                        .where(MarketBar.interval == "1m")
                        .where(MarketBar.ts >= ref_cutoff)
                        .order_by(MarketBar.ts.asc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if open_bar is None:
                    continue
                open_px = Decimal(open_bar.open or open_bar.close)
                if open_px <= 0:
                    continue

                cur_bar = await latest_bar(ses, symbol, interval="1m")
                if cur_bar is None:
                    continue
                last_px = Decimal(cur_bar.close)

                move = safe_pct(last_px - open_px, open_px)
                if abs(move) < self.move_threshold:
                    continue
                # Reversion: fade the move. Big drop → long; big spike → short.
                side = "long" if move < 0 else "short"

                magnitude = min(abs(move) / self.move_cap, Decimal("1"))
                confidence = max(Decimal("0.1"), magnitude)

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
                            f"intraday move {move * 100:.2f}% from session open "
                            f"{open_px} → {side} fade ({confidence:.3f})"
                        ),
                        context={
                            "session_open_px": str(open_px),
                            "last_px": str(last_px),
                            "move_pct": str(move),
                        },
                        tp_pct=self.tp_pct,
                        sl_pct=self.sl_pct,
                    )
                )
                logger.info(
                    f"{self.id}: {symbol} move={move * 100:.2f}% {side} conf={confidence:.3f}"
                )
        return drafts

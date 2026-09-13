"""US volume + price breakout v1 (long + short).

When a stock prints both (a) a 1m volume far above its recent average AND
(b) a new short-window extreme, momentum tends to continue briefly. Breakout
above the prior high → long; breakdown below the prior low → short (US allows
shorting). Confidence ~ excess volume ratio. Horizon: 15min.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger

from matrix_shared import session_scope

from strategy.base import PredictionDraft
from strategy.modules.us._helpers import (
    US_EXCHANGE,
    active_us_symbols,
    in_session,
    recent_bars,
)

STRATEGY_ID = "us_volume_breakout"
STRATEGY_VERSION = 1
WINDOW = 20  # bars
VOL_MULT = Decimal("3.0")  # current bar must exceed 3× the rolling average
VOL_MULT_CAP = Decimal("8.0")  # 8× → confidence 1.0
HORIZON_S = 900  # 15min
DEFAULT_TP_PCT = Decimal("0.030")
DEFAULT_SL_PCT = Decimal("0.015")


class UsVolumeBreakout:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "us"
    asset_class: str = "us"
    horizon_seconds: int = HORIZON_S

    def __init__(
        self,
        *,
        vol_mult: Decimal = VOL_MULT,
        vol_mult_cap: Decimal = VOL_MULT_CAP,
        horizon_s: int = HORIZON_S,
        tp_pct: Decimal | None = DEFAULT_TP_PCT,
        sl_pct: Decimal | None = DEFAULT_SL_PCT,
    ) -> None:
        self.vol_mult = vol_mult
        self.vol_mult_cap = vol_mult_cap
        self.horizon_seconds = horizon_s
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
                bars = await recent_bars(session, symbol, interval="1m", n=WINDOW)
                if len(bars) < WINDOW:
                    continue

                lookback = bars[:-1]
                current = bars[-1]

                vols = [Decimal(b.volume) for b in lookback]
                avg_vol = sum(vols) / Decimal(len(vols))
                if avg_vol == 0:
                    continue
                cur_vol = Decimal(current.volume)
                vol_ratio = cur_vol / avg_vol
                if vol_ratio < self.vol_mult:
                    continue

                highest_prior = max(Decimal(b.high) for b in lookback)
                lowest_prior = min(Decimal(b.low) for b in lookback)
                cur_close = Decimal(current.close)
                if cur_close > highest_prior:
                    side = "long"
                    ref = highest_prior
                elif cur_close < lowest_prior:
                    side = "short"
                    ref = lowest_prior
                else:
                    continue

                magnitude = min(vol_ratio / self.vol_mult_cap, Decimal("1"))
                confidence = max(Decimal("0.10"), magnitude)

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
                        entry_price_ref=cur_close,
                        generated_at=now,
                        thesis=(
                            f"vol×{vol_ratio:.1f} above {WINDOW}-bar avg + "
                            f"{'breakout' if side == 'long' else 'breakdown'} "
                            f"close {cur_close} vs {ref}"
                        ),
                        context={
                            "vol_ratio": str(vol_ratio),
                            "avg_vol": str(avg_vol),
                            "cur_vol": str(cur_vol),
                            "prior_high": str(highest_prior),
                            "prior_low": str(lowest_prior),
                            "window": WINDOW,
                        },
                        tp_pct=self.tp_pct,
                        sl_pct=self.sl_pct,
                    )
                )
                logger.info(
                    f"{self.id}: {symbol} {side} volx={vol_ratio:.1f} "
                    f"close={cur_close} ref={ref} conf={confidence:.3f}"
                )

        return drafts

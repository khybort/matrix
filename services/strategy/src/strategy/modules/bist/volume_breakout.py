"""BIST volume + price breakout v1.

Hypothesis: when an equity prints both (a) a 1m bar volume far above its
recent average AND (b) closes at a new short-window high, retail/algo
momentum tends to continue for a short horizon.

Implementation:
    For each active BIST symbol, read last N (default 20) 1m bars.
    avg_vol = mean(volume[:-1])
    breakout_high = max(high[:-1])
    last bar: volume > VOL_MULT * avg_vol AND close > breakout_high → long
    Confidence ~ excess volume ratio.

Long-only: only emit `long`.
Horizon: 15min.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger

from matrix_shared import session_scope

from strategy.base import PredictionDraft
from strategy.modules.bist._helpers import (
    BIST_EXCHANGE,
    active_bist_symbols,
    in_session,
    recent_bars,
)

STRATEGY_ID = "bist_volume_breakout"
STRATEGY_VERSION = 1
WINDOW = 20  # bars
VOL_MULT = Decimal("3.0")  # current bar must exceed 3× the rolling average
VOL_MULT_CAP = Decimal("8.0")  # 8× → confidence 1.0
HORIZON_S = 900  # 15min
DEFAULT_TP_PCT = Decimal("0.030")  # 3% take-profit
DEFAULT_SL_PCT = Decimal("0.015")  # 1.5% stop-loss


class BistVolumeBreakout:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "bist"
    asset_class: str = "bist"

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
            symbols = await active_bist_symbols(session)
            for symbol in symbols:
                bars = await recent_bars(session, symbol, interval="1m", n=WINDOW)  # WINDOW is fixed
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
                cur_close = Decimal(current.close)
                if cur_close <= highest_prior:
                    continue

                magnitude = min(vol_ratio / self.vol_mult_cap, Decimal("1"))
                confidence = max(Decimal("0.10"), magnitude)

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
                        entry_price_ref=cur_close,
                        generated_at=now,
                        thesis=(
                            f"vol×{vol_ratio:.1f} above {WINDOW}-bar avg + breakout "
                            f"close {cur_close} > prior high {highest_prior}"
                        ),
                        context={
                            "vol_ratio": str(vol_ratio),
                            "avg_vol": str(avg_vol),
                            "cur_vol": str(cur_vol),
                            "prior_high": str(highest_prior),
                            "window": WINDOW,
                        },
                        tp_pct=self.tp_pct,
                        sl_pct=self.sl_pct,
                    )
                )
                logger.info(
                    f"{self.id}: {symbol} long volx={vol_ratio:.1f} "
                    f"close={cur_close} prior_hi={highest_prior} conf={confidence:.3f}"
                )

        return drafts

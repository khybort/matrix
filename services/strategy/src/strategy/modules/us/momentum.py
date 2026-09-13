"""US cross-sectional momentum v1 (long + short).

Classic equity factor: over a trailing intraday window, the strongest names
tend to keep outperforming the weakest over a short continuation horizon. Rank
the active universe by trailing return, go long the top-K and short the
bottom-K. Confidence scales with the absolute return relative to the cohort.

Horizon: 90 minutes. K defaults to 5 per side to bound emissions and respect
the wallet's concurrent-position slots.
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
    safe_pct,
)

STRATEGY_ID = "us_momentum"
STRATEGY_VERSION = 1
WINDOW = 60  # 1m bars (~1h trailing window)
TOP_K = 5
MIN_RETURN = Decimal("0.01")  # ignore noise below ±1%
RETURN_CAP = Decimal("0.06")  # 6% trailing move → confidence 1.0
HORIZON_S = 5400  # 90min continuation
DEFAULT_TP_PCT = Decimal("0.025")
DEFAULT_SL_PCT = Decimal("0.015")


class UsMomentum:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "us"
    asset_class: str = "us"
    horizon_seconds: int = HORIZON_S

    def __init__(
        self,
        *,
        window: int = WINDOW,
        top_k: int = TOP_K,
        min_return: Decimal = MIN_RETURN,
        return_cap: Decimal = RETURN_CAP,
        horizon_s: int = HORIZON_S,
        tp_pct: Decimal | None = DEFAULT_TP_PCT,
        sl_pct: Decimal | None = DEFAULT_SL_PCT,
    ) -> None:
        self.window = window
        self.top_k = top_k
        self.min_return = min_return
        self.return_cap = return_cap
        self.horizon_seconds = horizon_s
        self.tp_pct = Decimal(str(tp_pct)) if tp_pct is not None else None
        self.sl_pct = Decimal(str(sl_pct)) if sl_pct is not None else None

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        if not in_session(now):
            return []

        scored: list[tuple[str, Decimal, Decimal]] = []  # (symbol, ret, last_px)
        async with session_scope() as session:
            symbols = await active_us_symbols(session)
            for symbol in symbols:
                bars = await recent_bars(session, symbol, interval="1m", n=self.window)
                if len(bars) < self.window:
                    continue
                first_px = Decimal(bars[0].close)
                last_px = Decimal(bars[-1].close)
                if first_px <= 0:
                    continue
                ret = safe_pct(last_px - first_px, first_px)
                if abs(ret) < self.min_return:
                    continue
                scored.append((symbol, ret, last_px))

        if not scored:
            return []

        scored.sort(key=lambda x: x[1], reverse=True)
        winners = scored[: self.top_k]
        losers = scored[-self.top_k :] if len(scored) > self.top_k else []

        drafts: list[PredictionDraft] = []

        def _emit(symbol: str, ret: Decimal, last_px: Decimal, side: str) -> None:
            magnitude = min(abs(ret) / self.return_cap, Decimal("1"))
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
                        f"trailing {self.window}-bar return {ret * 100:.2f}% "
                        f"→ {side} (cross-sectional momentum)"
                    ),
                    context={"trailing_return": str(ret), "window": self.window},
                    tp_pct=self.tp_pct,
                    sl_pct=self.sl_pct,
                )
            )
            logger.info(f"{self.id}: {symbol} ret={ret * 100:.2f}% {side} conf={confidence:.3f}")

        for symbol, ret, last_px in winners:
            if ret > 0:
                _emit(symbol, ret, last_px, "long")
        for symbol, ret, last_px in losers:
            if ret < 0:
                _emit(symbol, ret, last_px, "short")

        return drafts

"""Grid v1 — buy-the-dip / sell-the-rip across N price levels.

Hypothesis: in a sideways/mean-reverting regime, price oscillates inside a
band. Slicing that band into N evenly-spaced levels and fading every level
cross is a low-edge but high-frequency signal that the self-improvement
loop can score and tune.

Implementation:
    For each symbol:
      Pull the last 24h of 1m bars (asset_class='crypto') and derive a
      band from MAX(close) / MIN(close), tightened to ±DEFAULT_PRICE_BAND_PCT
      around the band midpoint.
      Slice the band into N_GRIDS levels.
      Look at the last LOOKBACK_S of bars: if a recent bar's close was
      above a grid line and the current price is below it → grid crossed
      down → LONG draft. Symmetric for up-cross → SHORT.
      Dedup against recent grid predictions for the same symbol+side in
      the SHARED predictions tier (DEDUP_WINDOW_S).
    Confidence scales with distance from band center; the deeper into the
    extreme, the higher the confidence (clamped 0.1..0.9).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import session_scope, shared_session_scope
from matrix_shared.models import MarketBar, MarketTrade, Prediction

from strategy.base import PredictionDraft

STRATEGY_ID = "grid"
STRATEGY_VERSION = 1
HORIZON_S = 300  # 5min
N_GRIDS = 10
DEDUP_WINDOW_S = 600  # 10min
LOOKBACK_S = 60  # look at last 60s of bars to detect a fresh cross
BAND_LOOKBACK_S = 24 * 3600  # 24h to size the band
DEFAULT_PRICE_BAND_PCT = Decimal("0.02")  # ±2% around the 24h median
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")


def _linspace(low: Decimal, high: Decimal, n: int) -> list[Decimal]:
    """Pure-Decimal linspace replacement (inclusive endpoints)."""
    if n < 2:
        return [low, high]
    step = (high - low) / Decimal(n - 1)
    return [low + step * Decimal(i) for i in range(n)]


class Grid:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    horizon_seconds: int = HORIZON_S

    def __init__(
        self,
        symbols: Sequence[str] = DEFAULT_SYMBOLS,
        band_pct: Decimal = DEFAULT_PRICE_BAND_PCT,
        n_grids: int = N_GRIDS,
    ) -> None:
        self.symbols = list(symbols)
        self.band_pct = band_pct
        self.n_grids = n_grids

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        band_since = now - timedelta(seconds=BAND_LOOKBACK_S)
        lookback_since = now - timedelta(seconds=LOOKBACK_S)
        drafts: list[PredictionDraft] = []

        async with session_scope() as local:
            for symbol in self.symbols:
                # ---- band: 24h close range
                band_stmt = (
                    select(MarketBar.close, MarketBar.ts)
                    .where(MarketBar.symbol == symbol)
                    .where(MarketBar.asset_class == "crypto")
                    .where(MarketBar.interval == "1m")
                    .where(MarketBar.ts >= band_since)
                    .order_by(MarketBar.ts.asc())
                )
                bars = (await local.execute(band_stmt)).all()
                if len(bars) < 2:
                    continue

                closes = [Decimal(b.close) for b in bars]
                hi_24h = max(closes)
                lo_24h = min(closes)
                if hi_24h == lo_24h:
                    continue
                mid = (hi_24h + lo_24h) / Decimal("2")
                half_band = mid * self.band_pct
                band_low = mid - half_band
                band_high = mid + half_band
                grid_lines = _linspace(band_low, band_high, self.n_grids)

                # ---- current price: latest trade else last bar close
                px_stmt = (
                    select(MarketTrade.price, MarketTrade.exchange)
                    .where(MarketTrade.symbol == symbol)
                    .order_by(desc(MarketTrade.trade_ts))
                    .limit(1)
                )
                px_row = (await local.execute(px_stmt)).first()
                if px_row is not None:
                    last_price = Decimal(px_row.price)
                    last_exchange = px_row.exchange
                else:
                    last_price = closes[-1]
                    last_exchange = "bybit"

                # ---- prior close (for cross detection): last bar inside LOOKBACK_S
                #      We compare the close of the most recent bar at-or-before
                #      lookback_since against the current price. If the prior
                #      close was above a grid line and current price is below
                #      → grid crossed down → LONG. Symmetric → SHORT.
                prior_close: Decimal | None = None
                for b in bars:
                    if b.ts <= lookback_since:
                        prior_close = Decimal(b.close)
                    else:
                        break
                if prior_close is None:
                    # All bars are inside the lookback window — use earliest.
                    prior_close = closes[0]

                side: str | None = None
                triggered_level: Decimal | None = None
                lvl_idx: int | None = None
                for i, lvl in enumerate(grid_lines):
                    if prior_close >= lvl > last_price:
                        # crossed down → LONG (buy the dip)
                        side = "long"
                        triggered_level = lvl
                        lvl_idx = i
                        break
                if side is None:
                    for i, lvl in enumerate(grid_lines):
                        if prior_close <= lvl < last_price:
                            # crossed up → SHORT (sell the rip)
                            side = "short"
                            triggered_level = lvl
                            lvl_idx = i
                            break
                if side is None or triggered_level is None or lvl_idx is None:
                    continue

                # ---- dedup against SHARED predictions in DEDUP_WINDOW_S
                dedup_since = now - timedelta(seconds=DEDUP_WINDOW_S)
                async with shared_session_scope() as shared:
                    dup_stmt = (
                        select(Prediction.id)
                        .where(Prediction.strategy_id == STRATEGY_ID)
                        .where(Prediction.symbol == symbol)
                        .where(Prediction.side == side)
                        .where(Prediction.generated_at >= dedup_since)
                        .limit(1)
                    )
                    dup = (await shared.execute(dup_stmt)).first()
                if dup is not None:
                    continue

                # ---- confidence: distance from band center, clamped 0.1..0.9
                # 0 at center, 1 at band edge.
                if half_band == 0:
                    distance_norm = Decimal("0")
                else:
                    distance_norm = min(
                        Decimal("1"),
                        abs(last_price - mid) / half_band,
                    )
                confidence = max(
                    Decimal("0.1"),
                    min(Decimal("0.9"), distance_norm),
                )

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
                            f"grid lvl {lvl_idx + 1}/{self.n_grids} "
                            f"{'below' if side == 'long' else 'above'}; "
                            f"px={last_price} grid={triggered_level} "
                            f"band=[{band_low},{band_high}]"
                        ),
                        context={
                            "n_grids": self.n_grids,
                            "band_pct": str(self.band_pct),
                            "band_low": str(band_low),
                            "band_high": str(band_high),
                            "mid": str(mid),
                            "hi_24h": str(hi_24h),
                            "lo_24h": str(lo_24h),
                            "triggered_level": str(triggered_level),
                            "level_index": lvl_idx,
                            "prior_close": str(prior_close),
                            "lookback_s": LOOKBACK_S,
                            "dedup_window_s": DEDUP_WINDOW_S,
                        },
                    )
                )
                logger.info(
                    f"{self.id}: signal {symbol} {side} conf={confidence:.3f} "
                    f"px={last_price} lvl={triggered_level} idx={lvl_idx}"
                )

        return drafts

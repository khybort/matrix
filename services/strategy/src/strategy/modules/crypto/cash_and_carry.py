"""Cash-and-carry (delta-neutral funding capture) v1.

Thesis: spot-long + perp-short pair captures the funding cash flow with
near-zero directional exposure. When perpetual funding is positive (longs
paying shorts), the synthetic position earns:

    pnl ≈ notional × (elapsed_hours / 8) × funding_rate_per_8h

We model the synthetic as a single PaperPosition with side='delta_neutral'
whose PnL accrues from the *live* funding rate, not from price moves.
Live execution (actual spot-buy + perp-short on exchange) is Phase-5 work;
this module is paper-trade only.

BTC funding ~0.51% / 8h ≈ 70% APY in 2026 (financefeeds.com, ainvest.com).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import Prediction, TickerSnapshot
from matrix_shared.markets.crypto import crypto_universe

from strategy.base import PredictionDraft

STRATEGY_ID = "cash_and_carry"
STRATEGY_VERSION = 1

# Minimum funding rate (per 8h) to enter; below this the APY is too thin
# to justify paper-trade capital.  Default: 0.01% / 8h ≈ ~14% APY.
DEFAULT_MIN_FUNDING = Decimal("0.0001")
# Funding rate that maps to confidence 1.0.
FUNDING_CAP = Decimal("0.0010")
# Outcome window matches the 8h funding interval.
DEFAULT_HORIZON_S = 28800  # 8 hours
DEFAULT_CONFIDENCE = Decimal("0.70")

# Dedup window: don't open a second position on the same symbol if one is
# already open (checked via predictions table, same strategy + symbol).
COOLDOWN_S = DEFAULT_HORIZON_S


class CashAndCarry:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "crypto"

    def __init__(
        self,
        symbols: Sequence[str] | None = None,
        *,
        min_funding: Decimal = DEFAULT_MIN_FUNDING,
        horizon_s: int = DEFAULT_HORIZON_S,
    ) -> None:
        self.symbols: list[str] = list(symbols) if symbols is not None else crypto_universe()
        self.min_funding = min_funding
        self.horizon_s = horizon_s

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        drafts: list[PredictionDraft] = []

        # Fetch the set of symbols that already have an open delta_neutral
        # prediction (cooldown gate).
        async with shared_session_scope() as shared:
            open_preds = (
                await shared.execute(
                    select(Prediction.symbol)
                    .where(Prediction.strategy_id == STRATEGY_ID)
                    .where(Prediction.side == "delta_neutral")
                    .where(Prediction.status == "open")
                )
            ).scalars().all()
        blocked_symbols: set[str] = set(open_preds)

        async with local_session_scope() as session:
            for symbol in self.symbols:
                if symbol in blocked_symbols:
                    logger.debug(f"{STRATEGY_ID}: {symbol} cooldown (open position exists)")
                    continue

                # Latest ticker with a non-null funding rate
                tk_stmt = (
                    select(
                        TickerSnapshot.funding_rate,
                        TickerSnapshot.exchange,
                        TickerSnapshot.last_price,
                        TickerSnapshot.mark_price,
                    )
                    .where(TickerSnapshot.symbol == symbol)
                    .where(TickerSnapshot.funding_rate.isnot(None))
                    .order_by(desc(TickerSnapshot.snapshot_ts))
                    .limit(1)
                )
                tk_row = (await session.execute(tk_stmt)).first()
                if tk_row is None:
                    continue

                fr = Decimal(tk_row.funding_rate)
                if fr < self.min_funding:
                    # Only enter when longs are paying shorts (positive funding)
                    # and the rate is above the profitability floor.
                    continue

                # Entry price reference — mark_price preferred for perp, fall
                # back to last_price if mark unavailable.
                ref_px = tk_row.mark_price or tk_row.last_price
                if ref_px is None:
                    continue
                ref_px = Decimal(ref_px)

                # Confidence scales with funding magnitude, capped at 1.0.
                confidence = min(fr / FUNDING_CAP, Decimal("1.0"))
                confidence = max(confidence, Decimal("0.10"))

                apy = fr * 3 * 365 * 100  # funding_rate is per-8h; 3/day × 365
                thesis = (
                    f"delta-neutral funding capture: funding={fr*100:.4f}% / 8h, "
                    f"projected {apy:.1f}% APY"
                )

                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange=tk_row.exchange,
                        side="delta_neutral",
                        confidence=confidence,
                        horizon_seconds=self.horizon_s,
                        entry_price_ref=ref_px,
                        generated_at=now,
                        thesis=thesis,
                        context={
                            "funding_rate_8h": str(fr),
                            "min_funding_threshold": str(self.min_funding),
                            "projected_apy_pct": f"{float(apy):.2f}",
                        },
                    )
                )
                logger.info(
                    f"{STRATEGY_ID}: signal {symbol} delta_neutral "
                    f"conf={confidence:.3f} funding={fr*100:.4f}%/8h apy={apy:.1f}%"
                )

        return drafts

"""Inverse carry (delta-neutral NEGATIVE-funding capture) v1.

Mirror image of cash_and_carry. When perpetual funding is *negative* (shorts
paying longs), the delta-neutral pair that EARNS is short-spot + long-perp:

    pnl ≈ notional × (elapsed_hours / 8) × |funding_rate_per_8h|

We model the synthetic as a single PaperPosition with side='inverse_carry'
whose PnL accrues from the *live* funding rate. The paper engine applies the
direction sign (-1 for inverse), so a funding of -0.08%/8h accrues POSITIVE.
Live execution (actual spot-sell + perp-long on exchange) is Phase-5 work;
this module is paper-trade only.

Economics match cash_and_carry: the two-leg carry pays a real round-trip cost
of ~2 × round_trip_cost_pct (≈30 bps on crypto), charged in the paper engine.
So we require |funding| ≥ 0.08%/8h and hold 48h (6 cycles) to amortise it.
Negative funding is rarer and usually shorter-lived than positive, so the
funding-flip early-exit (rate crosses back to ≥0) is the key downside guard.
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

STRATEGY_ID = "inverse_carry"
STRATEGY_VERSION = 1

# Minimum funding MAGNITUDE (per 8h) to enter — the rate must be at least this
# negative. Symmetric with cash_and_carry's 0.08%: break-even over the 48h hold
# is ~5 bps/8h against the ~30 bps two-leg cost, so 0.08% keeps a healthy margin
# and stays selective (only genuinely crowded-short funding).
DEFAULT_MIN_FUNDING = Decimal("0.0008")  # 0.08% / 8h magnitude
# Funding magnitude that maps to confidence 1.0.
FUNDING_CAP = Decimal("0.0020")  # 0.20% / 8h magnitude
# Hold across several funding cycles so the fixed two-leg round-trip cost is
# amortised over many funding payments. Funding-flip close caps downside if the
# rate turns non-negative mid-hold.
DEFAULT_HORIZON_S = 172800  # 48 hours (6 funding cycles)
DEFAULT_CONFIDENCE = Decimal("0.70")

# Dedup window: don't open a second position on the same symbol if one is
# already open (checked via predictions table, same strategy + symbol).
COOLDOWN_S = DEFAULT_HORIZON_S


class InverseCarry:
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

        # Symbols that already have an open inverse_carry prediction (cooldown).
        async with shared_session_scope() as shared:
            open_preds = (
                await shared.execute(
                    select(Prediction.symbol)
                    .where(Prediction.strategy_id == STRATEGY_ID)
                    .where(Prediction.strategy_version == self.version)
                    .where(Prediction.side == "inverse_carry")
                    .where(Prediction.status == "open")
                )
            ).scalars().all()
        blocked_symbols: set[str] = set(open_preds)

        async with local_session_scope() as session:
            for symbol in self.symbols:
                if symbol in blocked_symbols:
                    logger.debug(f"{STRATEGY_ID}: {symbol} cooldown (open position exists)")
                    continue

                tk_stmt = (
                    select(
                        TickerSnapshot.funding_rate,
                        TickerSnapshot.exchange,
                        TickerSnapshot.last_price,
                        TickerSnapshot.mark_price,
                    )
                    .where(TickerSnapshot.symbol == symbol)
                    .where(TickerSnapshot.exchange == "bybit")
                    .where(TickerSnapshot.funding_rate.isnot(None))
                    .order_by(desc(TickerSnapshot.snapshot_ts))
                    .limit(1)
                )
                tk_row = (await session.execute(tk_stmt)).first()
                if tk_row is None:
                    continue

                fr = Decimal(tk_row.funding_rate)
                # Enter only when funding is negative enough (shorts paying
                # longs by at least the profitability floor).
                if fr > -self.min_funding:
                    continue

                ref_px = tk_row.mark_price or tk_row.last_price
                if ref_px is None:
                    continue
                ref_px = Decimal(ref_px)

                mag = -fr  # positive magnitude
                confidence = min(mag / FUNDING_CAP, Decimal("1.0"))
                confidence = max(confidence, Decimal("0.10"))

                apy = mag * 3 * 365 * 100  # per-8h magnitude; 3/day × 365
                thesis = (
                    f"inverse delta-neutral funding capture: funding={fr*100:.4f}% / 8h, "
                    f"projected {apy:.1f}% APY (short-spot / long-perp)"
                )

                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange=tk_row.exchange,
                        side="inverse_carry",
                        confidence=confidence,
                        horizon_seconds=self.horizon_s,
                        entry_price_ref=ref_px,
                        generated_at=now,
                        thesis=thesis,
                        context={
                            # Signed funding (negative); the paper engine applies
                            # the -1 direction sign to make accrual positive.
                            "funding_rate_8h": str(fr),
                            "min_funding_threshold": str(self.min_funding),
                            "projected_apy_pct": f"{float(apy):.2f}",
                        },
                    )
                )
                logger.info(
                    f"{STRATEGY_ID}: signal {symbol} inverse_carry "
                    f"conf={confidence:.3f} funding={fr*100:.4f}%/8h apy={apy:.1f}%"
                )

        return drafts

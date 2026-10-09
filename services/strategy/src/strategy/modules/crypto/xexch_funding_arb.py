"""Cross-exchange funding-differential arbitrage v1.

The only cross-venue edge a retail paper system can honestly capture: not the
price spread (closes in milliseconds; needs co-located inventory on both books)
but the FUNDING-RATE DIFFERENTIAL. Two perps on the same underlying track the
same price, so a pair that is SHORT the high-funding venue and LONG the
low-funding venue is ~price-neutral and earns the difference every funding
cycle:

    pnl ≈ notional × (elapsed_hours / 8) × (f_short_venue − f_long_venue)

We model the synthetic as a single PaperPosition with side='xexch_carry'. The
paper engine accrues it from the LIVE differential (short/long venues stashed in
the draft context at open) and closes early if the differential collapses or
inverts (funding-flip guard). The two-leg round-trip cost (~2× round_trip) is
charged on close, same as the single-venue carries.

Data: bybit funding comes from the existing ticker WS feed; binance funding from
`ingestion.binance_funding` (mainnet public REST). Only symbols present under the
same name on BOTH venues are considered. Descoped for v1: price-spread arb (not
capturable) and per-symbol funding-interval normalisation (assumes 8h on both —
true for the major USDT perps we trade; a conservative min-diff + the flip guard
contain the residual risk).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.markets.crypto import crypto_universe
from matrix_shared.models import Prediction, TickerSnapshot

from strategy.base import PredictionDraft

STRATEGY_ID = "xexch_funding_arb"
STRATEGY_VERSION = 1

BYBIT = "bybit"
BINANCE = "binance"

# Minimum funding differential (per 8h) to enter. Break-even over the 48h hold
# is ~5 bps/8h against the ~30 bps two-leg cost; 0.05% keeps margin AND filters
# out phantom differentials from venue-to-venue noise (esp. testnet bybit vs
# mainnet binance). The flip guard closes if the differential collapses.
DEFAULT_MIN_DIFF = Decimal("0.0005")  # 0.05% / 8h
# Differential magnitude mapping to confidence 1.0.
DIFF_CAP = Decimal("0.0020")  # 0.20% / 8h
# Hold across several funding cycles to amortise the fixed two-leg cost.
DEFAULT_HORIZON_S = 172800  # 48 hours (6 funding cycles)

# Staleness guard: ignore a venue's funding snapshot older than this (a stale
# leg would produce a phantom differential). Both feeds refresh well inside it.
MAX_SNAPSHOT_AGE_S = 900  # 15 min

COOLDOWN_S = DEFAULT_HORIZON_S


class XexchFundingArb:
    id: str = STRATEGY_ID
    version: int = STRATEGY_VERSION
    market: str = "crypto"

    def __init__(
        self,
        symbols: Sequence[str] | None = None,
        *,
        min_diff: Decimal = DEFAULT_MIN_DIFF,
        horizon_s: int = DEFAULT_HORIZON_S,
    ) -> None:
        self.symbols: list[str] = list(symbols) if symbols is not None else crypto_universe()
        self.min_diff = min_diff
        self.horizon_s = horizon_s

    async def _latest(
        self, session, symbol: str, exchange: str, *, cutoff: datetime
    ) -> tuple[Decimal, Decimal | None] | None:
        """Latest (funding_rate, mark_price) for a symbol on a venue, or None if
        no fresh snapshot with a funding rate exists."""
        row = (
            await session.execute(
                select(
                    TickerSnapshot.funding_rate,
                    TickerSnapshot.mark_price,
                    TickerSnapshot.last_price,
                )
                .where(TickerSnapshot.symbol == symbol)
                .where(TickerSnapshot.exchange == exchange)
                .where(TickerSnapshot.funding_rate.isnot(None))
                .where(TickerSnapshot.snapshot_ts >= cutoff)
                .order_by(desc(TickerSnapshot.snapshot_ts))
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        return Decimal(row.funding_rate), (
            Decimal(row.mark_price) if row.mark_price is not None
            else (Decimal(row.last_price) if row.last_price is not None else None)
        )

    async def generate(self) -> list[PredictionDraft]:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=MAX_SNAPSHOT_AGE_S)
        drafts: list[PredictionDraft] = []

        async with shared_session_scope() as shared:
            open_preds = (
                await shared.execute(
                    select(Prediction.symbol)
                    .where(Prediction.strategy_id == STRATEGY_ID)
                    .where(Prediction.strategy_version == self.version)
                    .where(Prediction.side == "xexch_carry")
                    .where(Prediction.status == "open")
                )
            ).scalars().all()
        blocked_symbols: set[str] = set(open_preds)

        async with local_session_scope() as session:
            for symbol in self.symbols:
                if symbol in blocked_symbols:
                    logger.debug(f"{STRATEGY_ID}: {symbol} cooldown (open position exists)")
                    continue

                by = await self._latest(session, symbol, BYBIT, cutoff=cutoff)
                bn = await self._latest(session, symbol, BINANCE, cutoff=cutoff)
                if by is None or bn is None:
                    continue

                f_by, px_by = by
                f_bn, px_bn = bn
                diff = f_by - f_bn
                capture = abs(diff)
                if capture < self.min_diff:
                    continue

                # Short the venue with the HIGHER funding (it pays us), long the
                # lower one. capture = f_short − f_long > 0 by construction.
                short_venue = BYBIT if f_by > f_bn else BINANCE
                ref_px = px_by or px_bn
                if ref_px is None:
                    continue

                confidence = min(capture / DIFF_CAP, Decimal("1.0"))
                confidence = max(confidence, Decimal("0.10"))

                apy = capture * 3 * 365 * 100  # per-8h differential; 3/day × 365
                thesis = (
                    f"cross-exchange funding arb: bybit={f_by*100:.4f}% "
                    f"binance={f_bn*100:.4f}% Δ={diff*100:.4f}%/8h, short {short_venue}, "
                    f"projected {apy:.1f}% APY"
                )

                drafts.append(
                    PredictionDraft(
                        strategy_id=self.id,
                        strategy_version=self.version,
                        symbol=symbol,
                        exchange=short_venue,
                        side="xexch_carry",
                        confidence=confidence,
                        horizon_seconds=self.horizon_s,
                        entry_price_ref=ref_px,
                        generated_at=now,
                        thesis=thesis,
                        context={
                            # Positive, direction-signed differential the engine
                            # accrues (EV reads this; live accrual recomputes it
                            # from the two venues via xexch_short_venue).
                            "funding_diff_8h": str(capture),
                            "xexch_short_venue": short_venue,
                            "funding_bybit_8h": str(f_by),
                            "funding_binance_8h": str(f_bn),
                            "min_diff_threshold": str(self.min_diff),
                            "projected_apy_pct": f"{float(apy):.2f}",
                        },
                    )
                )
                logger.info(
                    f"{STRATEGY_ID}: signal {symbol} xexch_carry conf={confidence:.3f} "
                    f"Δ={diff*100:.4f}%/8h short={short_venue} apy={apy:.1f}%"
                )

        return drafts

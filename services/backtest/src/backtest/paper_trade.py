"""Paper-trade engine.

Watches `predictions` table. For each open prediction with no PaperPosition,
opens a synthetic position at the current best price for the symbol. When
`close_by` passes, marks it out at the then-current price and writes Outcome.

Pricing: uses the most recent `market_trades` row for the symbol/exchange.
If no trade is available within FRESHNESS_S, the prediction is skipped
(we'd be filling on stale data).

This is intentionally tiny. Realism (slippage, fees, multiple fills,
funding-rate accrual) is for later.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select

from matrix_shared import session_scope
from matrix_shared.models import MarketTrade, Outcome, PaperPosition, Prediction

NOTIONAL_USD = Decimal("100")  # tiny fixed notional per signal
FRESHNESS_S = 60
SLIPPAGE_BPS = Decimal("2")  # 2 bps each side as a placeholder cost


async def _latest_price(symbol: str) -> Decimal | None:
    """Return the most recent trade price within FRESHNESS_S seconds."""
    cutoff = datetime.now(UTC) - timedelta(seconds=FRESHNESS_S)
    async with session_scope() as session:
        stmt = (
            select(MarketTrade.price, MarketTrade.trade_ts)
            .where(MarketTrade.symbol == symbol)
            .where(MarketTrade.trade_ts >= cutoff)
            .order_by(MarketTrade.trade_ts.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        return Decimal(row.price) if row else None


def _apply_slippage(price: Decimal, side: str, *, opening: bool) -> Decimal:
    """Open: pay the spread. Close: pay the spread the other way."""
    bps = SLIPPAGE_BPS / Decimal("10000")
    if opening:
        return price * (Decimal("1") + bps) if side == "long" else price * (Decimal("1") - bps)
    else:
        return price * (Decimal("1") - bps) if side == "long" else price * (Decimal("1") + bps)


async def open_due_positions() -> int:
    """Open positions for predictions that have none yet."""
    async with session_scope() as session:
        # find predictions without a position
        stmt = (
            select(Prediction)
            .outerjoin(PaperPosition, PaperPosition.prediction_id == Prediction.id)
            .where(PaperPosition.id.is_(None))
            .where(Prediction.status == "open")
            .where(Prediction.side.in_(["long", "short"]))
        )
        rows = (await session.execute(stmt)).scalars().all()

    opened = 0
    for p in rows:
        last_px = await _latest_price(p.symbol)
        if last_px is None:
            logger.debug(f"skip {p.id}: no fresh price for {p.symbol}")
            continue
        entry = _apply_slippage(last_px, p.side, opening=True)
        async with session_scope() as session:
            session.add(
                PaperPosition(
                    prediction_id=p.id,
                    symbol=p.symbol,
                    exchange=p.exchange,
                    side=p.side,
                    notional_usd=NOTIONAL_USD,
                    opened_at=datetime.now(UTC),
                    opened_price=entry,
                    status="open",
                )
            )
        opened += 1
        logger.info(f"opened paper {p.side} {p.symbol} @ {entry:.4f} (pred={p.id})")
    return opened


async def close_due_positions() -> int:
    """Close positions whose prediction horizon has elapsed."""
    now = datetime.now(UTC)
    async with session_scope() as session:
        stmt = (
            select(PaperPosition, Prediction)
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.status == "open")
            .where(Prediction.close_by <= now)
        )
        rows = (await session.execute(stmt)).all()

    closed = 0
    for pos, pred in rows:
        last_px = await _latest_price(pos.symbol)
        if last_px is None:
            logger.debug(f"skip close {pos.id}: no fresh price for {pos.symbol}")
            continue
        exit_px = _apply_slippage(last_px, pos.side, opening=False)
        # PnL for fixed notional
        if pos.side == "long":
            pnl_pct = (exit_px - pos.opened_price) / pos.opened_price
        else:
            pnl_pct = (pos.opened_price - exit_px) / pos.opened_price
        pnl_usd = pos.notional_usd * pnl_pct

        # score: simple sigmoid-ish mapping of pnl_pct to [-1, 1]
        # cap returns at +/-1% horizon to avoid one-outlier domination
        capped_pct = max(min(pnl_pct, Decimal("0.01")), Decimal("-0.01"))
        score = capped_pct / Decimal("0.01")  # in [-1, 1]

        async with session_scope() as session:
            pos_db = await session.get(PaperPosition, pos.id)
            pos_db.closed_at = now
            pos_db.closed_price = exit_px
            pos_db.pnl_usd = pnl_usd
            pos_db.status = "closed"

            pred_db = await session.get(Prediction, pred.id)
            pred_db.status = "closed"

            session.add(
                Outcome(
                    prediction_id=pred.id,
                    observed_at=now,
                    pnl_usd=pnl_usd,
                    pnl_pct=pnl_pct,
                    score=score,
                    reason="hit_horizon",
                )
            )
        closed += 1
        logger.info(
            f"closed paper {pos.side} {pos.symbol} entry={pos.opened_price:.4f} "
            f"exit={exit_px:.4f} pnl={pnl_usd:.4f}USD ({pnl_pct*100:.3f}%) "
            f"score={score:.3f}"
        )
    return closed

"""Paper-trade engine, wallet-aware with risk-cap enforcement.

Tier routing:
    LOCAL  — market_trades read (for entry/exit pricing + unrealized mark)
    SHARED — wallets, paper_positions, predictions, outcomes

All decision/wallet state is shared across PCs via the SHARED tier so
multi-PC agents trade against a single virtual cuzdan. Market price reads
stay LOCAL because each PC ingests its own market feed.

See docs/TRADING.md for the risk rules this engine enforces.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import func, select, text

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.models import (
    MarketBar,
    MarketTrade,
    Outcome,
    PaperPosition,
    Prediction,
    Wallet,
    WalletSnapshot,
)

DEFAULT_WALLET_ID = uuid.UUID("00000000-0000-0000-0000-00000000d0e1")
FRESHNESS_S = 60                       # crypto: ticks every few seconds
FRESHNESS_S_BARS = 60 * 30             # BIST 1m bars + 15min Yahoo delay window
SLIPPAGE_BPS = Decimal("2")
SCORE_CAP_PCT = Decimal("0.01")  # ±1% horizon caps the score at ±1


async def _latest_price(symbol: str, asset_class: str = "crypto") -> Decimal | None:
    """Latest mark price for a symbol.

    crypto → MarketTrade (tick prints, sub-minute fresh)
    bist   → MarketBar 1m close (Yahoo-delayed, wider freshness window)
    """
    if asset_class == "bist":
        cutoff = datetime.now(UTC) - timedelta(seconds=FRESHNESS_S_BARS)
        async with local_session_scope() as session:
            stmt = (
                select(MarketBar.close)
                .where(MarketBar.symbol == symbol)
                .where(MarketBar.asset_class == "bist")
                .where(MarketBar.interval == "1m")
                .where(MarketBar.ts >= cutoff)
                .order_by(MarketBar.ts.desc())
                .limit(1)
            )
            row = (await session.execute(stmt)).first()
            return Decimal(row.close) if row else None

    cutoff = datetime.now(UTC) - timedelta(seconds=FRESHNESS_S)
    async with local_session_scope() as session:
        stmt = (
            select(MarketTrade.price)
            .where(MarketTrade.symbol == symbol)
            .where(MarketTrade.trade_ts >= cutoff)
            .order_by(MarketTrade.trade_ts.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        return Decimal(row.price) if row else None


def _apply_slippage(price: Decimal, side: str, *, opening: bool) -> Decimal:
    bps = SLIPPAGE_BPS / Decimal("10000")
    if opening:
        return price * (Decimal("1") + bps) if side == "long" else price * (Decimal("1") - bps)
    return price * (Decimal("1") - bps) if side == "long" else price * (Decimal("1") + bps)


def _unrealized_pnl(pos: PaperPosition, mark: Decimal) -> Decimal:
    if pos.side == "long":
        pnl_pct = (mark - pos.opened_price) / pos.opened_price
    else:
        pnl_pct = (pos.opened_price - mark) / pos.opened_price
    return pos.notional_usd * pnl_pct


async def _check_and_maybe_reset_day(wallet: Wallet, equity_now: Decimal) -> None:
    now = datetime.now(UTC)
    day_start = wallet.day_start_at
    if day_start.tzinfo is None:
        day_start = day_start.replace(tzinfo=UTC)
    if now.date() != day_start.date():
        wallet.day_start_at = now
        wallet.day_start_equity = equity_now
        if wallet.circuit_tripped_at is not None:
            logger.info(f"wallet {wallet.name}: daily reset; circuit untripped")
            wallet.circuit_tripped_at = None


def _circuit_should_trip(wallet: Wallet, equity_now: Decimal) -> bool:
    day_start = wallet.day_start_equity
    if day_start <= 0:
        return False
    drop = (day_start - equity_now) / day_start
    return drop >= wallet.daily_loss_circuit_pct


async def _peak_equity_today(session, wallet: Wallet) -> Decimal:
    """Highest equity_usd snapshot since day_start_at, floored at
    day_start_equity. The floor matters when the day just rolled and no
    snapshot exists yet — we don't want a one-tick wobble to trip the
    trailing stop against a phantom 'peak' below day_start."""
    stmt = (
        select(func.max(WalletSnapshot.equity_usd))
        .where(WalletSnapshot.wallet_id == wallet.id)
        .where(WalletSnapshot.snapshot_ts >= wallet.day_start_at)
    )
    peak = (await session.execute(stmt)).scalar()
    if peak is None:
        return Decimal(wallet.day_start_equity)
    return max(Decimal(peak), Decimal(wallet.day_start_equity))


def _trailing_stop_should_trip(
    wallet: Wallet, peak_equity: Decimal, equity_now: Decimal
) -> bool:
    pct = Decimal(wallet.equity_trailing_stop_pct)
    if pct <= 0 or peak_equity <= 0:
        return False
    drop = (peak_equity - equity_now) / peak_equity
    return drop >= pct


async def _current_equity(session, wallet: Wallet) -> tuple[Decimal, Decimal, int]:
    """Reads paper_positions from the SHARED session passed in, but pulls
    mark prices from the LOCAL tier (one query per open position).

    Returns (equity, unrealized_pnl, n_open_positions).
    """
    open_stmt = select(PaperPosition).where(
        PaperPosition.wallet_id == wallet.id, PaperPosition.status == "open"
    )
    open_positions = list((await session.execute(open_stmt)).scalars())

    unrealized = Decimal("0")
    for pos in open_positions:
        mark = await _latest_price(pos.symbol, pos.asset_class)
        if mark is None:
            continue
        unrealized += _unrealized_pnl(pos, mark)

    equity = wallet.cash_usd + wallet.locked_usd + unrealized
    return equity, unrealized, len(open_positions)


async def snapshot_wallet() -> None:
    """Write a WalletSnapshot for the default wallet; handle circuit breaker."""
    async with shared_session_scope() as session:
        wallet = await session.get(Wallet, DEFAULT_WALLET_ID)
        if wallet is None:
            logger.warning("default wallet not found, cannot snapshot")
            return

        equity, unrealized, n_open = await _current_equity(session, wallet)
        await _check_and_maybe_reset_day(wallet, equity)

        realized = wallet.cash_usd + wallet.locked_usd - wallet.starting_capital_usd

        session.add(
            WalletSnapshot(
                wallet_id=wallet.id,
                snapshot_ts=datetime.now(UTC),
                equity_usd=equity,
                cash_usd=wallet.cash_usd,
                locked_usd=wallet.locked_usd,
                n_open_positions=n_open,
                realized_pnl_usd=realized,
                unrealized_pnl_usd=unrealized,
            )
        )

        if wallet.circuit_tripped_at is None:
            if _circuit_should_trip(wallet, equity):
                wallet.circuit_tripped_at = datetime.now(UTC)
                logger.warning(
                    f"DAILY LOSS CIRCUIT TRIPPED for wallet {wallet.name}: "
                    f"day_start={wallet.day_start_equity:.2f} equity={equity:.2f}"
                )
            else:
                peak = await _peak_equity_today(session, wallet)
                if _trailing_stop_should_trip(wallet, peak, equity):
                    wallet.circuit_tripped_at = datetime.now(UTC)
                    logger.warning(
                        f"EQUITY TRAILING STOP TRIPPED for wallet {wallet.name}: "
                        f"peak={peak:.2f} equity={equity:.2f} "
                        f"stop_pct={wallet.equity_trailing_stop_pct}"
                    )


async def expire_stale_predictions() -> int:
    """Mark predictions whose close_by has passed without ever being traded.

    Without this, a backlog (e.g. after a backtest crash) accumulates as
    perpetually-'open' rows in `predictions` that no longer represent live
    signals. We don't write an Outcome — these predictions were never
    actually traded, so they shouldn't influence reflection metrics.
    """
    now = datetime.now(UTC)
    async with shared_session_scope() as session:
        # The `id NOT IN ...` subquery is fine here because the candidate set
        # is small (only open predictions); for >100k rows we'd switch to a
        # LEFT JOIN, but at current scale this stays readable.
        result = await session.execute(
            text(
                "UPDATE predictions SET status='expired' "
                "WHERE status='open' AND close_by < :now "
                "  AND id NOT IN (SELECT prediction_id FROM paper_positions) "
                "RETURNING id"
            ),
            {"now": now},
        )
        ids = list(result.scalars())
    if ids:
        logger.info(f"expired {len(ids)} stale predictions (never traded)")
    return len(ids)


async def open_due_positions() -> int:
    """Open positions for predictions that have none yet, respecting risk caps.

    All wallet/prediction state lives in SHARED; entry pricing is read from
    the LOCAL tier's market_trades."""
    async with shared_session_scope() as session:
        wallet = await session.get(Wallet, DEFAULT_WALLET_ID)
        if wallet is None:
            return 0
        if wallet.circuit_tripped_at is not None:
            logger.debug("circuit tripped; opening blocked")
            return 0

        open_count_stmt = (
            select(func.count(PaperPosition.id))
            .where(PaperPosition.wallet_id == wallet.id)
            .where(PaperPosition.status == "open")
        )
        open_count = (await session.execute(open_count_stmt)).scalar_one()
        slots_left = wallet.max_concurrent_positions - open_count
        if slots_left <= 0:
            return 0

        equity, _unrealized, _n = await _current_equity(session, wallet)
        max_notional = equity * wallet.max_position_pct

        # Skip predictions whose horizon already lapsed before we could open
        # them. Without this filter, a backlog (e.g. after a crash or queue
        # drain) causes backtest to open and immediately close stale
        # predictions on the very next tick — guaranteed slippage loss with
        # no real signal evaluation. Such predictions are flagged 'expired'
        # below instead, so they're scored once and removed from the queue.
        now = datetime.now(UTC)
        pred_stmt = (
            select(Prediction)
            .outerjoin(PaperPosition, PaperPosition.prediction_id == Prediction.id)
            .where(PaperPosition.id.is_(None))
            .where(Prediction.status == "open")
            .where(Prediction.side.in_(["long", "short"]))
            .where(Prediction.close_by > now)
            .order_by(Prediction.generated_at.asc())
            .limit(slots_left)
        )
        candidates = list((await session.execute(pred_stmt)).scalars())

    opened = 0
    for p in candidates:
        # BIST is long-only (T+2 settlement, retail short restrictions). The
        # strategy/agent layers already filter shorts, but this is a defensive
        # gate in case a buggy proposal slips through.
        if p.asset_class == "bist" and p.side == "short":
            logger.warning(f"skip {p.id}: short on BIST disallowed ({p.symbol})")
            continue

        last_px = await _latest_price(p.symbol, p.asset_class)
        if last_px is None:
            logger.debug(
                f"skip {p.id}: no fresh price for {p.symbol} ({p.asset_class})"
            )
            continue
        entry = _apply_slippage(last_px, p.side, opening=True)

        conf = max(Decimal("0.2"), min(Decimal("1.0"), p.confidence))
        notional = max_notional * conf
        notional = notional.quantize(Decimal("0.01"))

        async with shared_session_scope() as session:
            wallet = await session.get(Wallet, DEFAULT_WALLET_ID)
            if wallet is None:
                continue
            if wallet.circuit_tripped_at is not None:
                continue
            if wallet.cash_usd < notional:
                logger.info(f"skip {p.id}: insufficient cash ({wallet.cash_usd:.2f} < {notional})")
                continue
            wallet.cash_usd -= notional
            wallet.locked_usd += notional
            session.add(
                PaperPosition(
                    wallet_id=wallet.id,
                    prediction_id=p.id,
                    symbol=p.symbol,
                    exchange=p.exchange,
                    asset_class=p.asset_class,
                    side=p.side,
                    notional_usd=notional,
                    opened_at=datetime.now(UTC),
                    opened_price=entry,
                    status="open",
                )
            )
        opened += 1
        logger.info(
            f"opened {p.side} {p.symbol} [{p.asset_class}] notional={notional:.2f} "
            f"entry={entry:.4f} (pred={p.id}, strat={p.strategy_id}v{p.strategy_version})"
        )
    return opened


def _tp_sl_reason(pos: PaperPosition, pred: Prediction, mark: Decimal) -> str | None:
    """Return 'hit_tp' / 'hit_sl' if the current mark crosses the prediction's
    take-profit or stop-loss threshold for this position's side. None if no
    threshold trips. TP wins ties — strategies set tp_pct expecting profit
    realization, so when both fire on the same tick we honor the favorable
    side."""
    tp = pred.tp_pct
    sl = pred.sl_pct
    if tp is None and sl is None:
        return None
    if pos.side == "long":
        move = (mark - pos.opened_price) / pos.opened_price
        if tp is not None and move >= tp:
            return "hit_tp"
        if sl is not None and -move >= sl:
            return "hit_sl"
    else:  # short
        move = (pos.opened_price - mark) / pos.opened_price
        if tp is not None and move >= tp:
            return "hit_tp"
        if sl is not None and -move >= sl:
            return "hit_sl"
    return None


async def close_due_positions() -> int:
    now = datetime.now(UTC)
    async with shared_session_scope() as session:
        # Two close paths share the same write-side code below:
        #   1) tp/sl: any open position whose prediction has tp_pct or sl_pct
        #      set AND the current mark crosses the threshold (sign-aware).
        #   2) horizon: prediction's close_by has elapsed.
        # We compute path 1 first because it can fire BEFORE close_by, and a
        # position that hits TP should not also be counted on the horizon
        # query in the same call.
        tpsl_stmt = (
            select(PaperPosition, Prediction)
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.status == "open")
            .where(
                (Prediction.tp_pct.isnot(None)) | (Prediction.sl_pct.isnot(None))
            )
        )
        tpsl_rows = (await session.execute(tpsl_stmt)).all()

        horizon_stmt = (
            select(PaperPosition, Prediction)
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.status == "open")
            .where(Prediction.close_by <= now)
        )
        horizon_rows = (await session.execute(horizon_stmt)).all()

    # Evaluate tp/sl first; remember the position ids we already handled so the
    # horizon pass doesn't double-close them.
    work: list[tuple[PaperPosition, Prediction, str]] = []
    handled_ids: set[uuid.UUID] = set()
    for pos, pred in tpsl_rows:
        mark = await _latest_price(pos.symbol, pos.asset_class)
        if mark is None:
            continue
        reason = _tp_sl_reason(pos, pred, mark)
        if reason is None:
            continue
        work.append((pos, pred, reason))
        handled_ids.add(pos.id)
    for pos, pred in horizon_rows:
        if pos.id in handled_ids:
            continue
        work.append((pos, pred, "hit_horizon"))

    closed = 0
    for pos, pred, reason in work:
        last_px = await _latest_price(pos.symbol, pos.asset_class)
        if last_px is None:
            continue
        exit_px = _apply_slippage(last_px, pos.side, opening=False)
        if pos.side == "long":
            pnl_pct = (exit_px - pos.opened_price) / pos.opened_price
        else:
            pnl_pct = (pos.opened_price - exit_px) / pos.opened_price
        pnl_usd = pos.notional_usd * pnl_pct
        capped = max(min(pnl_pct, SCORE_CAP_PCT), -SCORE_CAP_PCT)
        score = capped / SCORE_CAP_PCT

        async with shared_session_scope() as session:
            pos_db = await session.get(PaperPosition, pos.id)
            pos_db.closed_at = now
            pos_db.closed_price = exit_px
            pos_db.pnl_usd = pnl_usd
            pos_db.status = "closed"

            pred_db = await session.get(Prediction, pred.id)
            pred_db.status = "closed"

            wallet = await session.get(Wallet, pos_db.wallet_id)
            if wallet is not None:
                wallet.locked_usd -= pos_db.notional_usd
                wallet.cash_usd += pos_db.notional_usd + pnl_usd

            session.add(
                Outcome(
                    prediction_id=pred.id,
                    asset_class=pos.asset_class,
                    observed_at=now,
                    pnl_usd=pnl_usd,
                    pnl_pct=pnl_pct,
                    score=score,
                    reason=reason,
                )
            )
        closed += 1
        logger.info(
            f"closed[{reason}] {pos.side} {pos.symbol} entry={pos.opened_price:.4f} "
            f"exit={exit_px:.4f} pnl={pnl_usd:.4f}USD ({pnl_pct*100:.3f}%) "
            f"score={score:.3f}"
        )
    return closed

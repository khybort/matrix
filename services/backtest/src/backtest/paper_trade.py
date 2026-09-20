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

import os

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError

from matrix_shared import local_session_scope, shared_session_scope
from matrix_shared.allocation import (
    expected_value,
    kelly_fraction_of_equity,
    kelly_notional,
    load_pair_edges,
    risk_multiplier,
)
from matrix_shared.exchange_shadow import shadow_close_position, shadow_open_position
from matrix_shared.graph_overlay import link_outcome_node
from matrix_shared.markets import all_markets
from matrix_shared.trading import (
    apply_slippage,
    execution_cost_bps,
    funding_pnl_usd,
    virtual_pnl_pct,
)
from matrix_shared.models import (
    StrategyConfig,
    MarketBar,
    MarketTrade,
    Outcome,
    PaperPosition,
    Prediction,
    TickerSnapshot,
    TradableSymbol,
    Wallet,
    WalletSnapshot,
)
from matrix_shared.models.slot_config import StrategySlotConfig

# Crypto's seed wallet (migration 0004). Per-market wallets are resolved by
# asset_class via _resolve_wallet; this constant remains the crypto default
# so the historical row keeps working.
DEFAULT_WALLET_ID = uuid.UUID("00000000-0000-0000-0000-00000000d0e1")


# Challenger (shadow) configs book their paper positions in a separate wallet
# per market so they never consume champion capital/slots, yet are priced and
# closed by exactly the same engine. reflection.efficacy compares the two.
SHADOW_WALLET_NAME = "shadow"


async def _resolve_wallet(session, asset_class: str, *, shadow: bool = False) -> Wallet | None:
    """The default (or shadow) wallet for a market. Each asset_class has its
    own capital pool + concurrent-position slots, so a flood of signals in one
    market can't starve another (Phase 1 fix: BIST gap_fade was filling the
    shared 5-slot pool and crowding crypto out of the candidate queue)."""
    stmt = select(Wallet).where(Wallet.asset_class == asset_class)
    if shadow:
        stmt = stmt.where(Wallet.name == SHADOW_WALLET_NAME)
    else:
        stmt = stmt.where(Wallet.name != SHADOW_WALLET_NAME)
    return (
        await session.execute(stmt.order_by(Wallet.created_at.asc()).limit(1))
    ).scalar_one_or_none()


async def ensure_shadow_wallets() -> int:
    """Create a `shadow` wallet (same caps as the default) for every market
    that has a default wallet. Idempotent; called at engine start."""
    created = 0
    markets = {m.asset_class for m in all_markets()}
    async with shared_session_scope() as session:
        wallets = list((await session.execute(select(Wallet).where(Wallet.asset_class.in_(markets)))).scalars())
        by_class: dict[str, list[Wallet]] = {}
        for w in wallets:
            by_class.setdefault(w.asset_class, []).append(w)
        for ac, ws in by_class.items():
            if any(w.name == SHADOW_WALLET_NAME for w in ws):
                continue
            base = sorted(ws, key=lambda w: w.created_at)[0]
            session.add(Wallet(
                name=SHADOW_WALLET_NAME,
                asset_class=ac,
                starting_capital_usd=base.starting_capital_usd,
                cash_usd=base.starting_capital_usd,
                locked_usd=Decimal("0"),
                max_position_pct=base.max_position_pct,
                max_concurrent_positions=base.max_concurrent_positions,
                daily_loss_circuit_pct=base.daily_loss_circuit_pct,
                day_start_equity=base.starting_capital_usd,
                day_start_at=datetime.now(UTC),
                equity_trailing_stop_pct=base.equity_trailing_stop_pct,
            ))
            created += 1
            logger.info(f"created shadow wallet for {ac} (caps copied from {base.name})")
    return created


def _is_shadow_prediction(p: Prediction) -> bool:
    return bool((p.context or {}).get("is_shadow", False))
FRESHNESS_S = 60                       # crypto: ticks every few seconds
FRESHNESS_S_BARS = 60 * 30             # BIST 1m bars + 15min Yahoo delay window
SLIPPAGE_BPS = Decimal("2")  # legacy flat allowance; live fills use FeeModel via matrix_shared.trading
SCORE_CAP_PCT = Decimal("0.01")  # ±1% horizon caps the score at ±1
# Positions whose close_by is more than this far in the past AND for which
# we have no live price are flat-closed to prevent indefinite orphan
# accumulation (the KONYA BIST bug: positions stuck 72h with no price data).
ORPHAN_STALE_THRESHOLD_S = 86400  # 24h past close_by
WALLET_SNAPSHOT_INTERVAL_S = float(os.environ.get("WALLET_SNAPSHOT_INTERVAL_S", "60"))


# A signal is only worth trading while it is still fresh. Measured 2026-09-20:
# the average fill happened 53% (momentum_xs), 41% (dca), 40% (grid) of the way
# through the prediction's own horizon, because unfilled predictions kept
# competing for slots until `close_by`. The controlled study
# (matrix_shared.edge_study) scored momentum_xs's *signals* +36 bps against
# random entry while its *fills* came in at −36 bps: the queue delay, not the
# signal, was destroying the edge. Candidates past MAX_SIGNAL_AGE_FRAC of their
# horizon are left unfilled (they still earn a virtual outcome, so the learning
# loop keeps the evidence for free), and EV decays with age so fresh candidates
# outrank stale ones.
# Markets priced from `market_bars` (Yahoo candles) rather than tick prints:
# crypto reads MarketTrade, equities read the 1m bar close. Referenced by
# `_latest_price` since 572966c but its definition never reached git — a
# latent NameError on any BIST/US mark, live only because the working tree
# carried it.
_BAR_PRICE_CLASSES = frozenset({"bist", "us"})

MAX_SIGNAL_AGE_FRAC = float(os.environ.get("MATRIX_MAX_SIGNAL_AGE_FRAC", "0.20"))
MIN_SIGNAL_WINDOW_S = float(os.environ.get("MATRIX_MIN_SIGNAL_WINDOW_S", "45"))


def signal_age_frac(generated_at: datetime, horizon_seconds: int | None, now: datetime) -> float:
    """How far into its own horizon a prediction already is (0 = brand new)."""
    horizon = float(horizon_seconds or 0)
    if horizon <= 0:
        return 0.0
    return max(0.0, (now - generated_at).total_seconds() / horizon)


def is_fresh_enough(generated_at: datetime, horizon_seconds: int | None, now: datetime) -> bool:
    """Trade it only if little of the horizon has burned. Short-horizon signals
    keep an absolute floor so they remain fillable at all."""
    horizon = float(horizon_seconds or 0)
    if horizon <= 0:
        return True
    elapsed = (now - generated_at).total_seconds()
    return elapsed <= max(horizon * MAX_SIGNAL_AGE_FRAC, MIN_SIGNAL_WINDOW_S)


async def _latest_funding_rate(symbol: str) -> Decimal | None:
    """Latest funding rate for a crypto perp from the LOCAL ticker snapshot table.

    Returns None if no snapshot exists — callers treat that as 0 accrual.
    We use the *live* rate rather than the rate-at-open so ongoing delta_neutral
    positions correctly reflect funding flips mid-hold.
    """
    async with local_session_scope() as session:
        stmt = (
            select(TickerSnapshot.funding_rate)
            .where(TickerSnapshot.symbol == symbol)
            .where(TickerSnapshot.funding_rate.isnot(None))
            .order_by(TickerSnapshot.snapshot_ts.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        return Decimal(row.funding_rate) if row else None


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


def _apply_slippage(
    price: Decimal,
    side: str,
    *,
    opening: bool,
    asset_class: str | None = None,
    symbol: str | None = None,
) -> Decimal:
    """Adverse fill: taker fee + slippage from the market's FeeModel
    (matrix_shared.trading). Without `asset_class` falls back to the legacy
    flat SLIPPAGE_BPS — only the historical replayer should hit that path."""
    return apply_slippage(price, side, opening=opening, asset_class=asset_class, symbol=symbol)


def _unrealized_pnl(
    pos: PaperPosition,
    mark: Decimal,
    *,
    funding_rate_8h: Decimal | None = None,
) -> Decimal:
    """Compute unrealized PnL for an open position.

    For delta_neutral positions the PnL is purely funding accrual:
        pnl = notional × (elapsed_hours / 8) × funding_rate_8h

    `funding_rate_8h` must be supplied by the caller for delta_neutral (read
    from the latest TickerSnapshot asynchronously before calling this function).
    If it is None the function returns 0 — callers ensure they fetch it first.

    For long/short positions the standard mark-vs-entry formula applies.
    """
    if pos.side == "delta_neutral":
        if funding_rate_8h is None:
            return Decimal("0")
        now = datetime.now(UTC)
        opened = pos.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=UTC)
        elapsed_hours = Decimal(str((now - opened).total_seconds() / 3600.0))
        return pos.notional_usd * (elapsed_hours / Decimal("8")) * funding_rate_8h

    if pos.side == "long":
        pnl_pct = (mark - pos.opened_price) / pos.opened_price
    else:
        pnl_pct = (pos.opened_price - mark) / pos.opened_price
    return pos.notional_usd * pnl_pct


def _live_enabled() -> bool:
    return os.environ.get("LIVE_EXECUTION_ENABLED", "false").strip().lower() == "true"


async def _check_and_maybe_reset_day(wallet: Wallet, equity_now: Decimal) -> None:
    """Roll the trading day. The daily-loss circuit auto-resets at the roll ONLY
    while the system is paper-only; with LIVE_EXECUTION_ENABLED=true a tripped
    circuit stays tripped until an operator resets it (`make circuit-reset`,
    Telegram /circuit_reset) — docs/TRADING.md hard limit #2."""
    now = datetime.now(UTC)
    day_start = wallet.day_start_at
    if day_start.tzinfo is None:
        day_start = day_start.replace(tzinfo=UTC)
    if now.date() != day_start.date():
        wallet.day_start_at = now
        wallet.day_start_equity = equity_now
        if wallet.circuit_tripped_at is not None:
            if _live_enabled():
                logger.warning(
                    f"wallet {wallet.name}/{wallet.asset_class}: day rolled but circuit stays "
                    "TRIPPED (live enabled) — manual reset required"
                )
            else:
                logger.info(f"wallet {wallet.name}: daily reset; circuit untripped (paper mode)")
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
        if pos.side == "delta_neutral":
            fr = await _latest_funding_rate(pos.symbol)
            # mark is irrelevant for delta_neutral; pass opened_price as a
            # placeholder so the function signature is satisfied.
            unrealized += _unrealized_pnl(pos, pos.opened_price, funding_rate_8h=fr)
        else:
            mark = await _latest_price(pos.symbol, pos.asset_class)
            if mark is None:
                continue
            unrealized += _unrealized_pnl(pos, mark)

    equity = wallet.cash_usd + wallet.locked_usd + unrealized
    return equity, unrealized, len(open_positions)


async def reconcile_wallets() -> int:
    """Self-heal the ledger invariant: `locked_usd == Σ(open paper_position notional)`.

    The wallet's cash/locked balances were historically mutated via an unguarded
    read-modify-write (no row lock, no version column). Under any writer overlap —
    e.g. an engine restart leaving two transient processes, or a manual script — a
    `close` decrement could be overwritten by a concurrent `open`, losing the
    release and leaving capital *phantom-locked* (locked_usd ratchets up while no
    open position backs it). This recomputes the true locked from open positions
    and moves any phantom back to cash.

    Equity (`cash + locked`) is preserved exactly: cash += delta, locked -= delta.
    This only relabels capital — it never changes realized PnL. Cheap (one aggregate
    per wallet); safe to call every tick. Returns the number of wallets corrected.
    """
    corrected = 0
    async with shared_session_scope() as session:
        rows = (
            await session.execute(select(Wallet.id, Wallet.name, Wallet.asset_class))
        ).all()
        for wid, name, asset_class in rows:
            real_locked = Decimal(
                (
                    await session.execute(
                        select(func.coalesce(func.sum(PaperPosition.notional_usd), 0))
                        .where(
                            PaperPosition.wallet_id == wid,
                            PaperPosition.status == "open",
                        )
                    )
                ).scalar_one()
            )
            # Atomic, guarded correction: recompute the true locked inside the
            # statement and move only the phantom (over-locked) portion back to
            # cash. The `locked_usd > :real` guard + the single UPDATE mean we
            # never clobber a concurrent open/close — if there's no phantom the
            # statement matches 0 rows. Equity (cash + locked) is preserved.
            res = await session.execute(
                text(
                    "UPDATE wallets SET "
                    "  cash_usd = cash_usd + (locked_usd - :real), "
                    "  locked_usd = :real "
                    "WHERE id = :wid AND locked_usd > :real"
                ),
                {"real": real_locked, "wid": wid},
            )
            if res.rowcount:
                corrected += 1
                logger.warning(
                    f"reconcile {name}/{asset_class}: phantom-locked returned to "
                    f"cash; locked → {real_locked:.2f} (= open notional)"
                )
    return corrected


async def snapshot_wallet() -> None:
    """Write a WalletSnapshot for every market's wallet; handle circuit breaker
    per wallet (one market tripping its daily-loss circuit must not freeze
    the others)."""
    markets = {m.asset_class for m in all_markets()}
    async with shared_session_scope() as session:
        wallets = list(
            (await session.execute(select(Wallet).where(Wallet.asset_class.in_(markets)))).scalars()
        )
    for w in wallets:
        try:
            await _snapshot_one(w.id)
        except IntegrityError as e:
            # Wallet vanished between the listing and the insert (test wallets
            # on a synthetic asset class live for seconds) — never let one
            # wallet's snapshot abort the whole tick for the real markets.
            logger.warning(f"snapshot skipped for wallet {w.id}: {str(e).splitlines()[0][:120]}")


async def _snapshot_one(wallet_id: uuid.UUID) -> None:
    async with shared_session_scope() as session:
        wallet = await session.get(Wallet, wallet_id)
        if wallet is None:
            return
        tripped_now = False

        equity, unrealized, n_open = await _current_equity(session, wallet)
        await _check_and_maybe_reset_day(wallet, equity)

        realized = wallet.cash_usd + wallet.locked_usd - wallet.starting_capital_usd

        # Persist the equity curve at most every WALLET_SNAPSHOT_INTERVAL_S
        # (default 60s). Circuit/trailing-stop logic below still runs every
        # tick off the live `equity` — only the row write is thinned (the 5s
        # cadence produced ~17k rows/wallet/day for nothing).
        last_ts = (await session.execute(
            select(func.max(WalletSnapshot.snapshot_ts))
            .where(WalletSnapshot.wallet_id == wallet.id)
        )).scalar()
        now_ts = datetime.now(UTC)
        if last_ts is None or (now_ts - last_ts).total_seconds() >= WALLET_SNAPSHOT_INTERVAL_S:
            session.add(
                WalletSnapshot(
                    wallet_id=wallet.id,
                    snapshot_ts=now_ts,
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
                tripped_now = True
                logger.warning(
                    f"DAILY LOSS CIRCUIT TRIPPED for wallet {wallet.name}: "
                    f"day_start={wallet.day_start_equity:.2f} equity={equity:.2f}"
                )
            else:
                peak = await _peak_equity_today(session, wallet)
                if _trailing_stop_should_trip(wallet, peak, equity):
                    wallet.circuit_tripped_at = datetime.now(UTC)
                    tripped_now = True
                    logger.warning(
                        f"EQUITY TRAILING STOP TRIPPED for wallet {wallet.name}: "
                        f"peak={peak:.2f} equity={equity:.2f} "
                        f"stop_pct={wallet.equity_trailing_stop_pct}"
                    )
    # TRADING.md hard limit #2: a trip closes every open position, not just
    # blocks new ones. Done after the wallet row committed so the flatten sees
    # the tripped state and its own session.
    if tripped_now:
        n = await flatten_wallet(wallet_id, reason="circuit_trip")
        logger.warning(f"circuit trip: flattened {n} open position(s) for wallet {wallet_id}")


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
        try:
            await _record_virtual_outcomes(ids)
        except Exception as e:  # noqa: BLE001 — counterfactuals are advisory
            logger.warning(f"virtual outcomes skipped: {e}")
    return len(ids)


async def _mark_near(symbol: str, asset_class: str, at: datetime) -> Decimal | None:
    """Historical mark closest to `at` (±120s): tick prints for crypto, 1m bar
    close for bar-priced markets."""
    lo, hi = at - timedelta(seconds=120), at + timedelta(seconds=120)
    async with local_session_scope() as session:
        if asset_class in _BAR_PRICE_CLASSES:
            row = (await session.execute(
                select(MarketBar.close).where(MarketBar.symbol == symbol)
                .where(MarketBar.asset_class == asset_class).where(MarketBar.interval == "1m")
                .where(MarketBar.ts >= lo - timedelta(minutes=30)).where(MarketBar.ts <= hi)
                .order_by(MarketBar.ts.desc()).limit(1)
            )).first()
        else:
            row = (await session.execute(
                select(MarketTrade.price).where(MarketTrade.symbol == symbol)
                .where(MarketTrade.trade_ts >= lo).where(MarketTrade.trade_ts <= hi)
                .order_by(func.abs(func.extract("epoch", MarketTrade.trade_ts) - func.extract("epoch", at)))
                .limit(1)
            )).first()
    return Decimal(row[0]) if row else None


async def _record_virtual_outcomes(pred_ids: list) -> int:
    """Counterfactual for signals the EV ranker never filled: what would the
    horizon exit have paid? Stored in predictions.context.virtual_outcome —
    NOT in `outcomes`, so metrics/lessons/certs stay realised-only. The
    Director digest compares it against realised PnL to grade the ranker
    (docs/AUTONOMY_PLAN.md P1.8)."""
    written = 0
    async with shared_session_scope() as session:
        preds = list((await session.execute(
            select(Prediction).where(Prediction.id.in_(pred_ids[:500]))
        )).scalars())
        for p in preds:
            if p.side not in ("long", "short"):
                continue
            close_by = p.close_by if p.close_by.tzinfo else p.close_by.replace(tzinfo=UTC)
            mark = await _mark_near(p.symbol, p.asset_class, close_by)
            if mark is None:
                continue
            pnl_pct = virtual_pnl_pct(entry_ref=Decimal(p.entry_price_ref), exit_mark=mark, side=p.side,
                                      asset_class=p.asset_class, symbol=p.symbol)
            if pnl_pct is None:
                continue
            ctx = dict(p.context or {})
            ctx["virtual_outcome"] = {
                "exit_price": str(mark), "pnl_pct": str(pnl_pct.quantize(Decimal("0.000001"))),
                "at": close_by.isoformat(), "basis": "horizon_exit_with_costs",
            }
            p.context = ctx
            written += 1
    if written:
        logger.info(f"virtual outcomes recorded for {written} untraded predictions")
    return written


# Last logged Kelly fraction per (strategy, market): this runs every tick and
# the number only moves when the 6 h edge cache refreshes.
_KELLY_LOGGED: dict[tuple[str, str], float] = {}


async def _kelly_fractions(
    strategy_ids: set[str], *, asset_class: str, concurrency: int
) -> dict[str, float]:
    """Quarter-Kelly fraction of equity per strategy, keyed by strategy_id.

    Only strategies the controlled study calls `pays` get an entry; everything
    else is absent from the dict and keeps the existing sizing. Advisory by
    construction: any failure in the study drops that strategy, it never fails
    the tick.
    """
    from matrix_shared.edge_study import strategy_edge, verdict

    out: dict[str, float] = {}
    for sid in strategy_ids:
        try:
            row = await strategy_edge(sid, asset_class)
        except Exception as e:  # noqa: BLE001 — sizing must never break opening
            logger.debug(f"kelly: edge study unavailable for {sid} ({e})")
            continue
        if not row:
            continue
        cost_bps = float(execution_cost_bps(asset_class) * 2)  # round trip
        if verdict(row, cost_bps=cost_bps) != "pays":
            continue
        # Size on whichever null the strategy actually beat: knowing *when* and
        # knowing *which way* are both edges, and `verdict` accepts either.
        if row.get("t_side", 0.0) > row.get("t", 0.0):
            edge_bps, t_stat = row.get("side_edge_bps", 0.0), row.get("t_side", 0.0)
        else:
            edge_bps, t_stat = row.get("edge_bps", 0.0), row.get("t", 0.0)
        f = kelly_fraction_of_equity(
            edge_bps=float(edge_bps),
            sd_bps=float(row.get("sd_bps") or 0.0),
            n=int(row.get("n") or 0),
            cost_bps=cost_bps,
            t_stat=float(t_stat),
            concurrency=int(concurrency or 1),
        )
        if f:
            out[sid] = f
            key = (sid, asset_class)
            if abs(_KELLY_LOGGED.get(key, -1.0) - f) > 1e-4:
                _KELLY_LOGGED[key] = f
                logger.info(
                    f"kelly {sid}/{asset_class}: {f * 100:.2f}% of equity "
                    f"(edge {edge_bps} bps, t={t_stat}, sd={row.get('sd_bps')}, "
                    f"n={row.get('n')}, concurrency={concurrency})"
                )
    return out


async def open_due_positions() -> int:
    """Open positions per market — each asset_class has its own wallet +
    concurrent-position slots, so one market's signal flood can't starve
    another's candidate queue."""
    total = 0
    for market in all_markets():
        total += await _open_for_market(market.asset_class)
        total += await _open_for_market(market.asset_class, shadow=True)
    return total


async def _open_for_market(asset_class: str, *, shadow: bool = False) -> int:
    """Open positions for one market's predictions, enforcing two-layer slot caps:
    1. Wallet-level: total open positions < wallet.max_concurrent_positions
    2. Per-strategy: open positions for strategy < config.allocated_slots

    All wallet/prediction state lives in SHARED; entry pricing is read from
    the LOCAL tier's market_trades / market_bars."""
    async with shared_session_scope() as session:
        wallet = await _resolve_wallet(session, asset_class, shadow=shadow)
        if wallet is None:
            # No wallet seeded for this market yet — skip silently. (BIST
            # gets one via `make bist-wallet-seed` / the 0019 data migration;
            # shadow wallets via ensure_shadow_wallets at engine start.)
            return 0
        if wallet.circuit_tripped_at is not None:
            logger.debug(f"circuit tripped ({asset_class}); opening blocked")
            return 0

        total_open = (
            await session.execute(
                select(func.count(PaperPosition.id))
                .where(PaperPosition.wallet_id == wallet.id)
                .where(PaperPosition.status == "open")
            )
        ).scalar_one()
        wallet_slots_left = wallet.max_concurrent_positions - total_open
        if wallet_slots_left <= 0:
            return 0

        equity, _unrealized, _n = await _current_equity(session, wallet)
        max_notional = equity * wallet.max_position_pct

        # Pre-fetch open counts per strategy for this wallet
        strategy_open_rows = (
            await session.execute(
                select(Prediction.strategy_id, func.count(PaperPosition.id))
                .join(Prediction, Prediction.id == PaperPosition.prediction_id)
                .where(PaperPosition.wallet_id == wallet.id)
                .where(PaperPosition.status == "open")
                .group_by(Prediction.strategy_id)
            )
        ).all()
        strategy_open: dict[str, int] = {sid: cnt for sid, cnt in strategy_open_rows}

        # Pre-fetch slot configs. The shadow (challenger) pass borrows the
        # CHAMPION wallet's per-strategy slots so a challenger runs under the
        # same capital discipline as the config it is being compared to —
        # otherwise it trades with no per-strategy cap and the champion/shadow
        # PnL comparison measures slot count, not parameters.
        slot_wallet_id = wallet.id
        if shadow:
            champion = await _resolve_wallet(session, asset_class)
            if champion is not None:
                slot_wallet_id = champion.id
        slot_configs: dict[str, StrategySlotConfig] = {
            cfg.strategy_id: cfg
            for cfg in (
                await session.execute(
                    select(StrategySlotConfig).where(
                        StrategySlotConfig.wallet_id == slot_wallet_id
                    )
                )
            ).scalars()
        }

        # Quarter-Kelly size for strategies whose edge survived both nulls.
        # Sizing by `risk_multiplier` alone is sizing by trailing realised PnL,
        # which is exactly the signal that broken fills corrupt. Kelly answers
        # a different question — how much does the *measured* edge justify —
        # and only ever raises size here, never suppresses a trade the slot
        # gate already allowed. The wallet's `max_position_pct` stays the
        # ceiling in every branch.
        kelly_f = await _kelly_fractions(
            {sid for sid in slot_configs},
            asset_class=wallet.asset_class,
            # The divisor is how many bets the book *actually* carries at once,
            # not the cap it is allowed to reach. Dividing by an 80-slot cap
            # that a ~9-position book never approaches understates every size
            # by an order of magnitude. Correlation between crypto perps is
            # handled by staying at a quarter of full Kelly, not by pretending
            # the book is denser than it is.
            concurrency=total_open + 1,
        )

        # Skip predictions whose horizon already lapsed before we could open
        # them. Without this filter, a backlog (e.g. after a crash or queue
        # drain) causes backtest to open and immediately close stale
        # predictions on the very next tick — guaranteed slippage loss with
        # no real signal evaluation. Such predictions are flagged 'expired'
        # below instead, so they're scored once and removed from the queue.
        now = datetime.now(UTC)
        # BIST is long-only; crypto also allows delta_neutral (funding capture).
        allowed_sides = (
            ["long", "short", "delta_neutral"] if asset_class == "crypto" else ["long"]
        )
        # Fetch a broad candidate pool (up to 5× slots) so the EV sort can pick
        # the best predictions rather than whichever happened to arrive first.
        pred_stmt = (
            select(Prediction)
            .outerjoin(PaperPosition, PaperPosition.prediction_id == Prediction.id)
            # A retired version's queued predictions must not be traded: after
            # a cutover/retire the old version kept filling slots and scoring
            # outcomes (452 open momentum_xs v1 predictions on 2026-09-13).
            .outerjoin(StrategyConfig, and_(
                StrategyConfig.strategy_id == Prediction.strategy_id,
                StrategyConfig.asset_class == Prediction.asset_class,
                StrategyConfig.version == Prediction.strategy_version,
            ))
            .where(or_(StrategyConfig.status.is_(None), StrategyConfig.status.in_(("active", "shadow"))))
            .where(PaperPosition.id.is_(None))
            .where(Prediction.status == "open")
            .where(Prediction.asset_class == asset_class)
            .where(Prediction.side.in_(allowed_sides))
            .where(Prediction.close_by > now)
            # Fresh signals only — see MAX_SIGNAL_AGE_FRAC.
            .where(
                text(
                    "extract(epoch from (:as_of - predictions.generated_at)) <= "
                    "greatest(predictions.horizon_seconds * :age_frac, :min_window)"
                )
            )
            .where(
                text("coalesce(predictions.context->>'is_shadow', 'false') = :is_shadow")
            )
            # Deterministic pool: highest-conviction, freshest first. Without an
            # ORDER BY the LIMIT took an arbitrary slice of the open queue, so
            # when a strategy floods predictions the best candidates (and any
            # fresh one) could be cut before the EV sort ever saw them.
            .order_by(Prediction.confidence.desc(), Prediction.generated_at.desc())
            .limit(wallet_slots_left * 5)
        )
        candidates_raw = list((await session.execute(
            pred_stmt,
            {
                "is_shadow": "true" if shadow else "false",
                "as_of": now,
                "age_frac": MAX_SIGNAL_AGE_FRAC,
                "min_window": MIN_SIGNAL_WINDOW_S,
            },
        )).scalars())

        # Batch-load per-symbol potential scores for the edge multiplier.
        # Symbols absent from tradable_symbols get score=None → neutral (1.0×).
        symbols_needed = {p.symbol for p in candidates_raw}
        strategy_ids_needed = {p.strategy_id for p in candidates_raw}
        symbol_scores: dict[str, float] = {}
        pair_edges: dict[tuple[str, str], float] = {}
        if symbols_needed:
            score_rows = (
                await session.execute(
                    select(TradableSymbol.symbol, TradableSymbol.score)
                    .where(TradableSymbol.asset_class == asset_class)
                    .where(TradableSymbol.symbol.in_(symbols_needed))
                )
            ).all()
            symbol_scores = {sym: sc for sym, sc in score_rows if sc is not None}
            pair_edges = await load_pair_edges(
                session,
                wallet_id=wallet.id,
                strategy_ids=strategy_ids_needed,
                symbols=symbols_needed,
            )

        def _ev(p: Prediction) -> float:
            tp = float(p.tp_pct) if p.tp_pct is not None else float(SCORE_CAP_PCT)
            sl = float(p.sl_pct) if p.sl_pct is not None else float(SCORE_CAP_PCT)
            cfg = slot_configs.get(p.strategy_id)
            ev = expected_value(
                confidence=float(p.confidence),
                tp_pct=tp,
                sl_pct=sl,
                symbol_edge=symbol_scores.get(p.symbol),
                strategy_perf=cfg.perf_score if cfg is not None else None,
                pair_edge=pair_edges.get((p.strategy_id, p.symbol)),
            )
            # Decay with age: what is left of the horizon is what can still be
            # earned, and a stale candidate must not outrank a fresh one just
            # because it was optimistic when it was born.
            return ev * max(0.0, 1.0 - signal_age_frac(p.generated_at, p.horizon_seconds, now))

        candidates = sorted(candidates_raw, key=_ev, reverse=True)

    # Track newly opened counts per strategy/symbol to enforce soft caps.
    newly_opened: dict[str, int] = {}
    symbol_open_count: dict[str, int] = {}
    opened = 0

    for p in candidates:
        if opened >= wallet_slots_left:
            break

        # Per-symbol soft cap: one symbol uses at most ceil(max_concurrent * share)
        # of the wallet slots. share grows with score so high-conviction symbols
        # can legitimately dominate, but a single noisy asset can't eat everything.
        sc = symbol_scores.get(p.symbol)
        sym_share = 0.15 + 0.20 * sc if sc is not None else 0.20
        sym_cap = max(1, int(wallet.max_concurrent_positions * sym_share + 0.5))
        if symbol_open_count.get(p.symbol, 0) >= sym_cap:
            continue
        # BIST is long-only (T+2 settlement, retail short restrictions). The
        # strategy/agent layers already filter shorts, but this is a defensive
        # gate in case a buggy proposal slips through.
        if p.asset_class == "bist" and p.side == "short":
            logger.warning(f"skip {p.id}: short on BIST disallowed ({p.symbol})")
            continue

        # Per-strategy slot gate (layer 2)
        cfg = slot_configs.get(p.strategy_id)
        if cfg is not None:
            current_open = strategy_open.get(p.strategy_id, 0) + newly_opened.get(p.strategy_id, 0)
            if current_open >= cfg.allocated_slots:
                logger.debug(
                    f"skip {p.id}: {p.strategy_id} at slot cap "
                    f"({current_open}/{cfg.allocated_slots})"
                )
                continue

        last_px = await _latest_price(p.symbol, p.asset_class)
        if last_px is None:
            logger.debug(
                f"skip {p.id}: no fresh price for {p.symbol} ({p.asset_class})"
            )
            continue
        entry = _apply_slippage(
            last_px, p.side, opening=True, asset_class=p.asset_class, symbol=p.symbol
        )

        cfg = slot_configs.get(p.strategy_id)
        conf = max(Decimal("0.05"), min(Decimal("1.0"), p.confidence))
        risk = risk_multiplier(
            confidence=conf,
            perf_score=cfg.perf_score if cfg is not None else None,
            pair_edge=pair_edges.get((p.strategy_id, p.symbol)),
            consecutive_losses=cfg.consecutive_losses if cfg is not None else 0,
        )
        notional = (max_notional * risk).quantize(Decimal("0.01"))
        # When the measured edge has an opinion, it *is* the size: betting
        # above Kelly lowers long-run growth just as surely as betting below
        # it does, and the confidence-and-streak multiplier was never a claim
        # about growth-optimal size. A zero fraction (the edge's lower bound
        # does not clear costs) is not an opinion — it keeps the old sizing
        # rather than silently suppressing a trade the slot gate allowed.
        kn = kelly_notional(
            equity=equity, max_notional=max_notional, kelly_f=kelly_f.get(p.strategy_id)
        )
        if kn:
            logger.debug(
                f"kelly size {p.strategy_id}: {notional} -> {kn} (gate {max_notional:.2f})"
            )
            notional = kn

        try:
            booked = await _book_position(p, notional=notional, entry=entry, shadow=shadow)
        except IntegrityError:
            # Another opener (a second engine tick, a test process) filled this
            # prediction between our candidate query and the insert. The
            # session rolled the debit back with the insert; skip, don't abort
            # the whole tick for the remaining candidates.
            logger.debug(f"skip {p.id}: position already booked by a concurrent opener")
            continue
        if not booked:
            continue
        wallet_id = booked
        newly_opened[p.strategy_id] = newly_opened.get(p.strategy_id, 0) + 1
        symbol_open_count[p.symbol] = symbol_open_count.get(p.symbol, 0) + 1
        opened += 1
        logger.info(
            f"opened {p.side} {p.symbol} [{p.asset_class}] notional={notional:.2f} "
            f"entry={entry:.4f} (pred={p.id}, strat={p.strategy_id}v{p.strategy_version})"
        )
        try:
            await shadow_open_position(
                prediction=p,
                wallet_id=wallet_id,
                entry_price=entry,
                notional_usd=notional,
            )
        except Exception as e:  # noqa: BLE001 — shadow must not break paper
            logger.warning(f"shadow open error pred={p.id}: {e}")
    return opened


async def _book_position(p: Prediction, *, notional: Decimal, entry: Decimal, shadow: bool):
    """Debit the wallet and insert the position in one transaction. Returns the
    wallet id, or None when the wallet is missing/tripped/underfunded. Raises
    IntegrityError if the prediction already has a position."""
    async with shared_session_scope() as session:
            # Same wallet the candidate pool was built for. Resolving without
            # `shadow` here booked every challenger position — with no
            # per-strategy cap — into the champion wallet (2026-09-13:
            # 1,198 shadow funding_reversion trades drained it by $343 in a day).
            wallet = await _resolve_wallet(session, p.asset_class, shadow=shadow)
            if wallet is None:
                return None
            if wallet.circuit_tripped_at is not None:
                return None
            # Atomic debit: a single conditional UPDATE (not read-modify-write)
            # so concurrent opens/closes can't lose each other's updates — the DB
            # serializes the row write. The `cash_usd >= :n` guard both enforces
            # the funding check and makes the debit conditional in one shot; if it
            # matches 0 rows there isn't enough cash, so we skip without booking a
            # position that no capital backs.
            res = await session.execute(
                text(
                    "UPDATE wallets SET cash_usd = cash_usd - :n, "
                    "locked_usd = locked_usd + :n "
                    "WHERE id = :wid AND cash_usd >= :n"
                ),
                {"n": notional, "wid": wallet.id},
            )
            if res.rowcount == 0:
                logger.info(f"skip {p.id}: insufficient cash for notional {notional}")
                return None
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
            wallet_id = wallet.id
    return wallet_id


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


async def _close_position(pos: PaperPosition, pred: Prediction, reason: str, now: datetime, *, force: bool = False) -> bool:
    """Close one open paper position at the current mark (with market costs),
    write its Outcome and release wallet capital atomically. Returns False when
    no fresh price exists and `force` is off (the caller retries next tick);
    with `force=True` (circuit trip) it flat-closes at entry instead.
    """
    if pos.side == "delta_neutral":
        # PnL is funding accrual over actual hold duration; no price-based
        # slippage. opened_at is the anchor; fr read live for correctness.
        fr = await _latest_funding_rate(pos.symbol)
        pnl_usd = _unrealized_pnl(pos, pos.opened_price, funding_rate_8h=fr)
        # For scoring: express PnL as % of notional (analogous to pnl_pct
        # for directional positions).
        pnl_pct = pnl_usd / pos.notional_usd if pos.notional_usd else Decimal("0")
        # Score is capped at ±1 relative to SCORE_CAP_PCT.
        capped = max(min(pnl_pct, SCORE_CAP_PCT), -SCORE_CAP_PCT)
        score = capped / SCORE_CAP_PCT
        exit_px = pos.opened_price  # synthetic — no actual sell
    else:
        last_px = await _latest_price(pos.symbol, pos.asset_class)
        if last_px is None:
            # Orphan-close: position's close_by is ORPHAN_STALE_THRESHOLD_S
            # past due AND we still have no price. Flat-close at entry to
            # prevent indefinite accumulation (the KONYA BIST 72h bug).
            close_by = pred.close_by
            if close_by.tzinfo is None:
                close_by = close_by.replace(tzinfo=UTC)
            age_past_close = (now - close_by).total_seconds()
            if force or (reason == "hit_horizon" and age_past_close > ORPHAN_STALE_THRESHOLD_S):
                exit_px = pos.opened_price  # flat — no real exit price available
                pnl_pct = Decimal("0")
                pnl_usd = Decimal("0")
                score = Decimal("0")
                reason = reason if force else "orphan_flat_close"
            else:
                return False
        else:
            exit_px = _apply_slippage(
                last_px, pos.side, opening=False,
                asset_class=pos.asset_class, symbol=pos.symbol,
            )
            if pos.side == "long":
                pnl_pct = (exit_px - pos.opened_price) / pos.opened_price
            else:
                pnl_pct = (pos.opened_price - exit_px) / pos.opened_price
            pnl_usd = pos.notional_usd * pnl_pct
            # Perp funding over the hold: longs pay a positive rate, shorts
            # receive it. Uses the latest 8h rate as the hold-average proxy.
            if pos.asset_class == "crypto":
                opened = pos.opened_at if pos.opened_at.tzinfo else pos.opened_at.replace(tzinfo=UTC)
                elapsed_h = Decimal(str((now - opened).total_seconds() / 3600.0))
                fr = await _latest_funding_rate(pos.symbol)
                pnl_usd += funding_pnl_usd(pos.side, pos.notional_usd, fr, elapsed_h)
                pnl_pct = pnl_usd / pos.notional_usd if pos.notional_usd else pnl_pct
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

        # Atomic release: single UPDATE so a concurrent open can't clobber the
        # decrement (the lost-update that caused phantom-locked capital). The
        # status flip, prediction close, wallet release and Outcome all commit
        # together in this one transaction.
        await session.execute(
            text(
                "UPDATE wallets SET locked_usd = locked_usd - :n, "
                "cash_usd = cash_usd + :n + :pnl "
                "WHERE id = :wid"
            ),
            {"n": pos_db.notional_usd, "pnl": pnl_usd, "wid": pos_db.wallet_id},
        )

        outcome_id = uuid.uuid4()
        session.add(
            Outcome(
                id=outcome_id,
                prediction_id=pred.id,
                asset_class=pos.asset_class,
                observed_at=now,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                score=score,
                reason=reason,
            )
        )
    logger.info(
        f"closed[{reason}] {pos.side} {pos.symbol} entry={pos.opened_price:.4f} "
        f"exit={exit_px:.4f} pnl={pnl_usd:.4f}USD ({pnl_pct*100:.3f}%) "
        f"score={score:.3f}"
    )
    try:
        await shadow_close_position(prediction=pred, position=pos)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"shadow close error pos={pos.id}: {e}")
    # Reasoning overlay: Prediction -[RESULTED_IN]-> Outcome (best-effort index).
    await link_outcome_node(
        pred_id=str(pred.id), outcome_id=str(outcome_id), score=str(score),
        pnl_usd=str(pnl_usd), reason=reason,
    )
    return True


async def close_due_positions() -> int:
    now = datetime.now(UTC)
    async with shared_session_scope() as session:
        # Three close paths share the same write-side code below:
        #   1) tp/sl: any open position whose prediction has tp_pct or sl_pct
        #      set AND the current mark crosses the threshold (sign-aware).
        #      Not applicable to delta_neutral (no price-based TP/SL).
        #   2) horizon: prediction's close_by has elapsed.
        #   3) funding-flip: delta_neutral positions where the live funding
        #      rate has turned negative (longs no longer paying shorts).
        # Path 1 is evaluated first; handled_ids prevents double-close.
        tpsl_stmt = (
            select(PaperPosition, Prediction)
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.status == "open")
            .where(PaperPosition.side != "delta_neutral")
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

        # Funding-flip candidates: delta_neutral positions whose horizon has
        # NOT yet elapsed (those are caught by the horizon query above).
        funding_flip_stmt = (
            select(PaperPosition, Prediction)
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.status == "open")
            .where(PaperPosition.side == "delta_neutral")
            .where(Prediction.close_by > now)
        )
        funding_flip_rows = (await session.execute(funding_flip_stmt)).all()

    # --- path 1: TP / SL ---
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

    # --- path 2: horizon ---
    for pos, pred in horizon_rows:
        if pos.id in handled_ids:
            continue
        work.append((pos, pred, "hit_horizon"))
        handled_ids.add(pos.id)

    # --- path 3: funding-flip early exit ---
    for pos, pred in funding_flip_rows:
        if pos.id in handled_ids:
            continue
        fr = await _latest_funding_rate(pos.symbol)
        if fr is not None and fr < Decimal("0"):
            work.append((pos, pred, "funding_flip"))
            handled_ids.add(pos.id)

    closed = 0
    for pos, pred, reason in work:
        if await _close_position(pos, pred, reason, now):
            closed += 1
    return closed


async def flatten_wallet(wallet_id: uuid.UUID, *, reason: str = "circuit_trip") -> int:
    """Close EVERY open position of a wallet now (circuit trip / operator kill).
    Uses the normal close path (mark price + costs + Outcome + capital release);
    positions without a fresh mark are flat-closed at entry so nothing stays open."""
    now = datetime.now(UTC)
    async with shared_session_scope() as session:
        rows = (await session.execute(
            select(PaperPosition, Prediction)
            .join(Prediction, Prediction.id == PaperPosition.prediction_id)
            .where(PaperPosition.wallet_id == wallet_id)
            .where(PaperPosition.status == "open")
        )).all()
        for pos, pred in rows:
            session.expunge(pos)
            session.expunge(pred)
    closed = 0
    for pos, pred in rows:
        try:
            if await _close_position(pos, pred, reason, now, force=True):
                closed += 1
        except Exception as e:  # noqa: BLE001 — keep flattening the rest
            logger.exception(f"flatten: close failed for {pos.id}: {e}")
    return closed

"""Historical replayer for the matrix_agent rule-based decision path.

The live agent (services/agent) blends ~5 feature signals into a single
score, thresholds it, and emits long/short/hold decisions. To backtest
that against historical data we need two things:

  1. An "as of timestamp T" version of `extract_symbol_features` —
     same DB queries the live agent uses, but with explicit `now=T`
     instead of `datetime.now()`. Done inline in `_features_at`.
  2. A pure-math copy of the score functions + rule_decide. We do NOT
     import these from services/agent because:
       - services/agent is FORBIDDEN_PATHS for dev_agent autonomous edits;
         keeping a clean separation prevents accidental coupling.
       - matrix-agent as a path dep would also pull in matrix-graph,
         which the backtest runtime doesn't otherwise need.
     The drift risk (live scoring diverges from replay) is documented
     here and tests pin the math.

LLM path is NOT simulated. matrix_agent in production overrides the rule
decision with an LLM call when available; replaying that historically
would require a different LLM call per bar (cost-prohibitive). The
replay is therefore a faithful test of the RULE path only.

Graph signals are also NOT replayed yet. graph_signals is a point-in-time
snapshot table but the underlying AGE graph evolves; without a
time-travel query layer, we set graph_* features to zero. news_score's
keyword-fallback still fires off raw_documents headlines (queryable
historically).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import desc, select

from matrix_shared import local_session_scope
from matrix_shared.models import (
    MarketBar,
    MarketTrade,
    OrderBookSnapshot,
    RawDocument,
    TickerSnapshot,
)

from backtest.historical import (
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_STARTING_CAPITAL,
    BacktestPosition,
    BacktestResult,
    _apply_slippage,
    _infer_bar_seconds,
    _max_drawdown_pct,
)


# ----------------------------------------------------------------- features


@dataclass(slots=True)
class SymbolFeatures:
    """Mirror of services/agent/src/agent/features.SymbolFeatures.

    Kept narrow to what the rule_decide math actually consumes — the live
    feature dataclass carries extras for LLM context that the replay
    doesn't need.
    """
    symbol: str
    last_price: Decimal | None = None
    n_trades_60s: int = 0
    buy_share_60s: Decimal = Decimal("0.5")
    notional_60s_usd: Decimal = Decimal("0")
    spread_bps: Decimal | None = None
    bid_ask_imbalance_top5: Decimal | None = None
    funding_rate: Decimal | None = None
    open_interest: Decimal | None = None
    oi_delta_pct_5m: Decimal | None = None
    n_news_1h: int = 0
    news_titles_sample: list[str] = field(default_factory=list)
    # Graph features stay defaulted — see module docstring.
    graph_mention_count: int = 0
    graph_recency_weight: Decimal = Decimal("0")
    graph_direct_polarity: Decimal = Decimal("0")
    graph_contextual_polarity: Decimal = Decimal("0")
    graph_related_companies: list[str] = field(default_factory=list)
    graph_co_mentioned_assets: list[str] = field(default_factory=list)


async def _features_at(symbol: str, now: datetime) -> SymbolFeatures:
    """Feature extraction with an explicit `now` instead of datetime.now().

    Same shape and queries as services/agent.features.extract_symbol_features
    minus the graph layer. Inputs live in LOCAL tier; no shared DB hit.
    """
    f = SymbolFeatures(symbol=symbol)

    async with local_session_scope() as session:
        since = now - timedelta(seconds=60)
        stmt = (
            select(MarketTrade.side, MarketTrade.size, MarketTrade.price)
            .where(MarketTrade.symbol == symbol)
            .where(MarketTrade.trade_ts >= since)
            .where(MarketTrade.trade_ts <= now)
        )
        trades = (await session.execute(stmt)).all()
        if trades:
            buy_vol = sum((Decimal(r.size) for r in trades if r.side == "buy"), Decimal(0))
            sell_vol = sum((Decimal(r.size) for r in trades if r.side == "sell"), Decimal(0))
            total = buy_vol + sell_vol
            if total > 0:
                f.buy_share_60s = buy_vol / total
            f.n_trades_60s = len(trades)
            last_px = Decimal(trades[-1].price)
            f.last_price = last_px
            f.notional_60s_usd = total * last_px

        ob_stmt = (
            select(OrderBookSnapshot)
            .where(OrderBookSnapshot.symbol == symbol)
            .where(OrderBookSnapshot.snapshot_ts <= now)
            .order_by(desc(OrderBookSnapshot.snapshot_ts))
            .limit(1)
        )
        ob = (await session.execute(ob_stmt)).scalar_one_or_none()
        if ob is not None and ob.bids and ob.asks:
            try:
                best_bid = Decimal(str(ob.bids[0][0]))
                best_ask = Decimal(str(ob.asks[0][0]))
                mid = (best_bid + best_ask) / Decimal("2")
                if mid > 0:
                    f.spread_bps = (best_ask - best_bid) / mid * Decimal("10000")
                bids_sz = sum(Decimal(str(b[1])) for b in ob.bids[:5])
                asks_sz = sum(Decimal(str(a[1])) for a in ob.asks[:5])
                tot = bids_sz + asks_sz
                if tot > 0:
                    f.bid_ask_imbalance_top5 = bids_sz / tot
            except (ValueError, IndexError, ArithmeticError):
                pass

        tk_stmt = (
            select(TickerSnapshot)
            .where(TickerSnapshot.symbol == symbol)
            .where(TickerSnapshot.snapshot_ts <= now)
            .order_by(desc(TickerSnapshot.snapshot_ts))
            .limit(1)
        )
        tk = (await session.execute(tk_stmt)).scalar_one_or_none()
        if tk is not None:
            f.funding_rate = tk.funding_rate
            f.open_interest = tk.open_interest
            if tk.open_interest:
                older = now - timedelta(minutes=5)
                older_stmt = (
                    select(TickerSnapshot.open_interest)
                    .where(TickerSnapshot.symbol == symbol)
                    .where(TickerSnapshot.snapshot_ts <= older)
                    .order_by(desc(TickerSnapshot.snapshot_ts))
                    .limit(1)
                )
                old_oi_row = (await session.execute(older_stmt)).first()
                if old_oi_row and old_oi_row[0]:
                    old_oi = Decimal(old_oi_row[0])
                    if old_oi > 0:
                        f.oi_delta_pct_5m = (tk.open_interest - old_oi) / old_oi

        base = _base_token(symbol)
        keywords = [base.lower()]
        if base == "BTC":
            keywords += ["bitcoin"]
        elif base == "ETH":
            keywords += ["ethereum"]
        news_since = now - timedelta(hours=1)
        news_stmt = (
            select(RawDocument.title)
            .where(RawDocument.published_at >= news_since)
            .where(RawDocument.published_at <= now)
            .order_by(desc(RawDocument.published_at))
            .limit(50)
        )
        news_rows = (await session.execute(news_stmt)).all()
        relevant = [
            r.title
            for r in news_rows
            if r.title and any(k in r.title.lower() for k in keywords)
        ]
        f.n_news_1h = len(relevant)
        f.news_titles_sample = relevant[:5]

    return f


def _base_token(symbol: str) -> str:
    for q in ("USDT", "USDC", "USD", "BUSD"):
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return symbol


# ----------------------------------------------------------------- scoring


# Defaults match agent/decide.WEIGHTS for the active matrix_agent strategy.
DEFAULT_WEIGHTS: dict[str, Decimal] = {
    "trade_flow": Decimal("0.35"),
    "funding": Decimal("0.20"),
    "oi_delta": Decimal("0.20"),
    "ob_imbalance": Decimal("0.15"),
    "news": Decimal("0.10"),
}
DEFAULT_SIGNAL_THRESHOLD = Decimal("0.18")


def _trade_flow_score(f: SymbolFeatures) -> Decimal:
    if f.n_trades_60s < 5:
        return Decimal("0")
    raw = (f.buy_share_60s - Decimal("0.5")) * Decimal("2")
    return -raw


def _funding_score(f: SymbolFeatures) -> Decimal:
    if f.funding_rate is None:
        return Decimal("0")
    scaled = f.funding_rate / Decimal("0.0005")
    scaled = max(min(scaled, Decimal("1")), Decimal("-1"))
    return -scaled


def _oi_score(f: SymbolFeatures) -> Decimal:
    if f.oi_delta_pct_5m is None:
        return Decimal("0")
    scaled = f.oi_delta_pct_5m / Decimal("0.02")
    return max(min(scaled, Decimal("1")), Decimal("-1"))


def _ob_imbalance_score(f: SymbolFeatures) -> Decimal:
    if f.bid_ask_imbalance_top5 is None:
        return Decimal("0")
    return (f.bid_ask_imbalance_top5 - Decimal("0.5")) * Decimal("2")


def _news_score_fallback(f: SymbolFeatures) -> Decimal:
    """Keyword polarity on news_titles_sample (graph path skipped in replay)."""
    if not f.news_titles_sample:
        return Decimal("0")
    bullish_kw = (
        "surge", "rally", "soar", "breakout", "approve", "etf", "adoption",
        "rises", "bullish", "high",
    )
    bearish_kw = (
        "crash", "plunge", "drop", "ban", "hack", "exploit", "lawsuit",
        "investigation", "bearish", "selloff", "liquidat",
    )
    pos = 0
    neg = 0
    for t in f.news_titles_sample:
        tl = t.lower()
        pos += sum(1 for k in bullish_kw if k in tl)
        neg += sum(1 for k in bearish_kw if k in tl)
    if pos + neg == 0:
        return Decimal("0")
    return Decimal(pos - neg) / Decimal(pos + neg)


def rule_decide(
    f: SymbolFeatures,
    weights: dict[str, Decimal],
    signal_threshold: Decimal,
) -> tuple[str, Decimal, str]:
    """Returns (side, confidence, thesis).

    Mirrors agent/decide.rule_decide. asset_class crypto-only here; non-crypto
    branches would zero out funding/oi but we only replay BTC/ETH right now.
    """
    sub = {
        "trade_flow": _trade_flow_score(f),
        "funding": _funding_score(f),
        "oi_delta": _oi_score(f),
        "ob_imbalance": _ob_imbalance_score(f),
        "news": _news_score_fallback(f),
    }
    total = sum((weights.get(k, Decimal("0")) * v for k, v in sub.items()), Decimal("0"))

    if total >= signal_threshold:
        side = "long"
        conf = min(Decimal("1"), total)
    elif total <= -signal_threshold:
        side = "short"
        conf = min(Decimal("1"), abs(total))
    else:
        side = "hold"
        conf = Decimal("0")

    thesis = (
        f"score={total:.3f} | "
        + " | ".join(f"{k}={v:.2f}" for k, v in sub.items())
    )
    return side, conf, thesis


# ----------------------------------------------------------------- replayer


# Defaults reflect agent/main loop interval (15s tick) — but at 15s on 1m
# bars we'd be running ~4 ticks per bar with the same feature set. Use 5min
# step in backtests; close enough to capture regime changes, fast enough
# that a 7-day window finishes inside the dashboard preview timeout.
DEFAULT_STEP_BARS = 5
DEFAULT_HORIZON_S = 120


def _coerce_weights(raw: dict[str, Any] | None) -> dict[str, Decimal]:
    if not raw:
        return dict(DEFAULT_WEIGHTS)
    return {k: Decimal(str(v)) for k, v in raw.items()}


async def matrix_agent_replay(
    bars: list[MarketBar],
    params: dict[str, Any],
    *,
    symbol_hint: str | None = None,
    starting_capital: Decimal = DEFAULT_STARTING_CAPITAL,
    max_position_pct: Decimal = DEFAULT_MAX_POSITION_PCT,
) -> BacktestResult:
    """Walk bars at step_bars intervals; at each step extract features as of
    that bar's ts, call rule_decide, simulate entry, close at next horizon.
    """
    if not bars:
        return BacktestResult(
            strategy_id="matrix_agent",
            params=params,
            window=(datetime.now(timezone.utc), datetime.now(timezone.utc)),
            n_bars=0, n_predictions=0, n_positions_opened=0, n_positions_closed=0,
            avg_pnl_usd=Decimal("0"), total_pnl_usd=Decimal("0"),
            win_rate=Decimal("0"), max_drawdown_pct=Decimal("0"),
        )

    symbol = symbol_hint or bars[0].symbol
    weights = _coerce_weights(params.get("weights"))
    threshold = Decimal(str(params.get("signal_threshold", DEFAULT_SIGNAL_THRESHOLD)))
    step_bars = int(params.get("step_bars", DEFAULT_STEP_BARS))
    horizon_s = int(params.get("horizon_s", DEFAULT_HORIZON_S))
    bar_seconds = _infer_bar_seconds(bars)
    horizon_bars = max(1, horizon_s // bar_seconds)
    notional = (starting_capital * max_position_pct).quantize(Decimal("0.01"))

    positions: list[BacktestPosition] = []
    n_predictions = 0
    equity_curve: list[Decimal] = [starting_capital]
    realized = Decimal("0")

    # Track open positions by their close_bar index so we can settle them
    # when the loop catches up. tuples: (close_idx, side, opened_price, opened_ts)
    open_pos: list[tuple[int, str, Decimal, datetime]] = []

    for i in range(0, len(bars), step_bars):
        bar = bars[i]
        # Settle anything due before this step
        still_open: list[tuple[int, str, Decimal, datetime]] = []
        for close_idx, side, opened_price, opened_ts in open_pos:
            if close_idx <= i:
                close_bar = bars[min(close_idx, len(bars) - 1)]
                exit_px = _apply_slippage(close_bar.close, side, opening=False)
                if side == "long":
                    pnl_pct = (exit_px - opened_price) / opened_price
                else:
                    pnl_pct = (opened_price - exit_px) / opened_price
                pnl = (notional * pnl_pct).quantize(Decimal("0.000001"))
                realized += pnl
                positions.append(BacktestPosition(
                    side=side, opened_ts=opened_ts, opened_price=opened_price,
                    closed_ts=close_bar.ts, closed_price=exit_px,
                    notional_usd=notional, pnl_usd=pnl,
                ))
                equity_curve.append(starting_capital + realized)
            else:
                still_open.append((close_idx, side, opened_price, opened_ts))
        open_pos = still_open

        # Feature extract + decide
        try:
            f = await _features_at(symbol, bar.ts)
        except Exception as e:
            logger.warning(f"feature extract failed at {bar.ts}: {e}")
            continue
        side, _conf, _thesis = rule_decide(f, weights, threshold)
        if side == "hold":
            continue
        n_predictions += 1
        entry_px = _apply_slippage(bar.close, side, opening=True)
        open_pos.append((i + horizon_bars, side, entry_px, bar.ts))

    # Final settle: anything still open closes at the last bar.
    last_idx = len(bars) - 1
    for _, side, opened_price, opened_ts in open_pos:
        last_bar = bars[last_idx]
        exit_px = _apply_slippage(last_bar.close, side, opening=False)
        if side == "long":
            pnl_pct = (exit_px - opened_price) / opened_price
        else:
            pnl_pct = (opened_price - exit_px) / opened_price
        pnl = (notional * pnl_pct).quantize(Decimal("0.000001"))
        realized += pnl
        positions.append(BacktestPosition(
            side=side, opened_ts=opened_ts, opened_price=opened_price,
            closed_ts=last_bar.ts, closed_price=exit_px,
            notional_usd=notional, pnl_usd=pnl,
        ))
        equity_curve.append(starting_capital + realized)

    n_closed = len(positions)
    wins = sum(1 for p in positions if p.pnl_usd > 0)
    avg = (realized / Decimal(n_closed)) if n_closed else Decimal("0")
    win_rate = (Decimal(wins) / Decimal(n_closed)) if n_closed else Decimal("0")

    return BacktestResult(
        strategy_id="matrix_agent",
        params={
            "weights": {k: str(v) for k, v in weights.items()},
            "signal_threshold": str(threshold),
            "step_bars": step_bars,
            "horizon_s": horizon_s,
        },
        window=(bars[0].ts, bars[-1].ts),
        n_bars=len(bars),
        n_predictions=n_predictions,
        n_positions_opened=n_closed,
        n_positions_closed=n_closed,
        avg_pnl_usd=avg,
        total_pnl_usd=realized,
        win_rate=win_rate,
        max_drawdown_pct=_max_drawdown_pct(equity_curve),
        positions=positions,
    )

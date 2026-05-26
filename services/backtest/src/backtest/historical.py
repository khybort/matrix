"""Historical replay engine — bar-by-bar backtest against `market_bars`.

The live `paper_trade.py` opens positions against *real-time* predictions; it
cannot validate a parameter change before shipping. This module replays a
strategy's signal logic against historical bars from the LOCAL tier and
returns equity-curve / P&L statistics so we can certify a strategy (or a
parameter mutation) before it ever touches the live wallet.

Scope of this first cut: ONE adapter — `grid_replay` — which mirrors the
soon-to-be-shipped Grid strategy (Bybit's flagship bot type, only needs
OHLC bars, no order-book/trade replay required). Other strategies that
depend on tick data / funding / order-book snapshots will get their own
adapters in later commits.

Conventions (must match live paper engine):
    SLIPPAGE_BPS    = 2 (entry + exit, symmetric, see paper_trade.py)
    starting_capital = $10,000 reference (only used as P&L / drawdown scale)
    max_position_pct = 2%        (matches Wallet default cap)

Everything is `Decimal` — no float money. No DB writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from matrix_shared import local_session_scope
from matrix_shared.models import MarketBar

# ----------------------------------------------------------------- constants

SLIPPAGE_BPS = Decimal("2")           # identical to paper_trade.py
DEFAULT_STARTING_CAPITAL = Decimal("10000")
DEFAULT_MAX_POSITION_PCT = Decimal("0.02")
ROLLING_WINDOW_BARS = 1440             # 24h of 1-minute bars


# ----------------------------------------------------------------- dataclasses


@dataclass(slots=True)
class BacktestPosition:
    side: str                         # "long" | "short"
    opened_ts: datetime
    opened_price: Decimal
    closed_ts: datetime | None
    closed_price: Decimal | None
    notional_usd: Decimal
    pnl_usd: Decimal
    close_reason: str = "hit_horizon"  # "hit_horizon" | "hit_tp" | "hit_sl" | "end_of_window"


@dataclass(slots=True)
class BacktestResult:
    strategy_id: str
    params: dict[str, Any]
    window: tuple[datetime, datetime]
    n_bars: int
    n_predictions: int
    n_positions_opened: int
    n_positions_closed: int
    avg_pnl_usd: Decimal
    total_pnl_usd: Decimal
    win_rate: Decimal
    max_drawdown_pct: Decimal
    positions: list[BacktestPosition] = field(default_factory=list)


# ----------------------------------------------------------------- bar fetcher


async def fetch_bars(
    symbol: str,
    asset_class: str,
    interval: str,
    since: datetime,
    until: datetime,
) -> list[MarketBar]:
    """Fetch ordered 1m (or other interval) bars for [since, until] from LOCAL."""
    async with local_session_scope() as session:
        stmt = (
            select(MarketBar)
            .where(MarketBar.symbol == symbol)
            .where(MarketBar.asset_class == asset_class)
            .where(MarketBar.interval == interval)
            .where(MarketBar.ts >= since)
            .where(MarketBar.ts <= until)
            .order_by(MarketBar.ts.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        # Detach from the session so closing the scope doesn't expire the
        # attributes mid-replay.
        for r in rows:
            session.expunge(r)
        return list(rows)


# ----------------------------------------------------------------- helpers


def _apply_slippage(price: Decimal, side: str, *, opening: bool) -> Decimal:
    """Identical to paper_trade._apply_slippage. Long entries pay up, exits
    sell down; shorts mirror."""
    bps = SLIPPAGE_BPS / Decimal("10000")
    if opening:
        return price * (Decimal("1") + bps) if side == "long" else price * (Decimal("1") - bps)
    return price * (Decimal("1") - bps) if side == "long" else price * (Decimal("1") + bps)


def _simulate_exit(
    bars: list[MarketBar],
    open_idx: int,
    side: str,
    entry_price: Decimal,
    horizon_bars: int,
    *,
    tp_pct: Decimal | None = None,
    sl_pct: Decimal | None = None,
) -> tuple[int, Decimal, str]:
    """Walk bars after `open_idx` until tp_pct OR sl_pct triggers, OR the
    horizon expires, OR we run out of bars.

    Returns (close_idx, exit_price_with_slippage, close_reason).

    Semantics mirror the live engine (services/backtest/paper_trade.py):
      - tp_pct / sl_pct are fractions in [0, 1]. Sign-aware per side.
      - tp_pct in long means price went UP by tp_pct; in short means DOWN.
      - sl_pct in long means price went DOWN by sl_pct; in short means UP.
      - On the same bar where both could trigger, TP wins (a chart gap
        means the operator-favorable outcome is what we report — matches
        paper_trade._tp_sl_reason).
      - Reasons: "hit_tp" / "hit_sl" / "hit_horizon" / "end_of_window".

    Slippage is applied to the exit price the same way live close does.
    """
    if not bars:
        return open_idx, entry_price, "end_of_window"

    horizon_idx = min(open_idx + horizon_bars, len(bars) - 1)
    last_idx = len(bars) - 1

    # Walk from the FIRST bar after open. The opening bar itself doesn't
    # close the trade — that would be zero-time hold and useless.
    for i in range(open_idx + 1, min(horizon_idx + 1, last_idx + 1)):
        bar = bars[i]
        if side == "long":
            move_for = (bar.close - entry_price) / entry_price
            move_against = -move_for
        else:
            move_for = (entry_price - bar.close) / entry_price
            move_against = -move_for

        # TP wins ties (see docstring).
        if tp_pct is not None and move_for >= tp_pct:
            return i, _apply_slippage(bar.close, side, opening=False), "hit_tp"
        if sl_pct is not None and move_against >= sl_pct:
            return i, _apply_slippage(bar.close, side, opening=False), "hit_sl"

    # No trigger inside the horizon — close at the horizon bar (or the
    # last available bar if we ran out before reaching horizon).
    close_idx = horizon_idx if horizon_idx <= last_idx else last_idx
    close_bar = bars[close_idx]
    reason = "hit_horizon" if close_idx == open_idx + horizon_bars else "end_of_window"
    return close_idx, _apply_slippage(close_bar.close, side, opening=False), reason


def _grid_lines(mid: Decimal, band_pct: Decimal, n_grids: int) -> list[Decimal]:
    """Evenly distributed price levels in [mid*(1-band), mid*(1+band)]."""
    lo = mid * (Decimal("1") - band_pct)
    hi = mid * (Decimal("1") + band_pct)
    if n_grids < 2:
        return [mid]
    step = (hi - lo) / Decimal(n_grids - 1)
    return [lo + step * Decimal(i) for i in range(n_grids)]


# ----------------------------------------------------------------- grid replay


def grid_replay(
    bars: list[MarketBar],
    params: dict[str, Any],
    *,
    starting_capital: Decimal = DEFAULT_STARTING_CAPITAL,
    max_position_pct: Decimal = DEFAULT_MAX_POSITION_PCT,
    slippage_bps: int = 2,
) -> BacktestResult:
    """Bar-by-bar grid replay.

    Long-only (matches BIST + most Bybit grid presets). On each bar:
      1. If we have < ROLLING_WINDOW_BARS history, just warm up.
      2. Compute mid = mean(close) over the rolling window.
      3. Build N grid lines across mid ± price_band_pct.
      4. If the bar's close crossed BELOW a grid line that the prior bar's
         close was above → emit a long signal, open position at close*(1+slip).
      5. Each open position closes when its horizon_s elapses; exit price is
         the bar at-or-after exit_ts, with reverse slippage applied.

    horizon_s is in seconds and aligned to bar boundaries (1-minute bars =
    60 s buckets); fractional bars are rounded up so a 300 s horizon = 5 bars.

    No defensive validation: caller passes a sane param dict per CLI / API.
    """
    n_grids = int(params.get("n_grids", 10))
    band_pct = Decimal(str(params.get("price_band_pct", "0.02")))
    horizon_s = int(params.get("horizon_s", 300))

    if not bars:
        return BacktestResult(
            strategy_id="grid",
            params=params,
            window=(datetime.now(UTC), datetime.now(UTC)),
            n_bars=0,
            n_predictions=0,
            n_positions_opened=0,
            n_positions_closed=0,
            avg_pnl_usd=Decimal("0"),
            total_pnl_usd=Decimal("0"),
            win_rate=Decimal("0"),
            max_drawdown_pct=Decimal("0"),
            positions=[],
        )

    slip = Decimal(slippage_bps) / Decimal("10000")
    bar_seconds = _infer_bar_seconds(bars)
    horizon_bars = max(1, (horizon_s + bar_seconds - 1) // bar_seconds)
    tp_pct = params.get("tp_pct")
    sl_pct = params.get("sl_pct")
    tp_pct = Decimal(str(tp_pct)) if tp_pct is not None else None
    sl_pct = Decimal(str(sl_pct)) if sl_pct is not None else None

    window: deque[Decimal] = deque(maxlen=ROLLING_WINDOW_BARS)
    notional = (starting_capital * max_position_pct).quantize(Decimal("0.01"))

    positions: list[BacktestPosition] = []
    equity_curve: list[Decimal] = [starting_capital]
    realized = Decimal("0")
    n_predictions = 0
    prev_close: Decimal | None = None

    for i, bar in enumerate(bars):
        close = Decimal(bar.close)

        # Rolling-window grid crossover — one entry per bar at most.
        if len(window) >= ROLLING_WINDOW_BARS and prev_close is not None:
            mid = sum(window, Decimal("0")) / Decimal(len(window))
            lines = _grid_lines(mid, band_pct, n_grids)
            for line in lines:
                if prev_close > line >= close:
                    n_predictions += 1
                    entry_px = close * (Decimal("1") + slip)
                    close_idx, exit_px, reason = _simulate_exit(
                        bars, i, "long", entry_px, horizon_bars,
                        tp_pct=tp_pct, sl_pct=sl_pct,
                    )
                    pnl_pct = (exit_px - entry_px) / entry_px
                    pnl = (notional * pnl_pct).quantize(Decimal("0.000001"))
                    realized += pnl
                    positions.append(BacktestPosition(
                        side="long",
                        opened_ts=bar.ts,
                        opened_price=entry_px,
                        closed_ts=bars[close_idx].ts,
                        closed_price=exit_px,
                        notional_usd=notional,
                        pnl_usd=pnl,
                        close_reason=reason,
                    ))
                    equity_curve.append(starting_capital + realized)
                    break  # one entry per bar; avoid stacking on noisy ticks

        window.append(close)
        prev_close = close

    # ---- stats
    n_closed = sum(1 for p in positions if p.closed_ts is not None)
    wins = sum(1 for p in positions if p.pnl_usd > 0)
    total = sum((p.pnl_usd for p in positions), Decimal("0"))
    avg = (total / Decimal(n_closed)).quantize(Decimal("0.000001")) if n_closed else Decimal("0")
    win_rate = (Decimal(wins) / Decimal(n_closed)).quantize(Decimal("0.0001")) if n_closed else Decimal("0")
    max_dd = _max_drawdown_pct(equity_curve)

    return BacktestResult(
        strategy_id="grid",
        params=params,
        window=(bars[0].ts, bars[-1].ts),
        n_bars=len(bars),
        n_predictions=n_predictions,
        n_positions_opened=len(positions),
        n_positions_closed=n_closed,
        avg_pnl_usd=avg,
        total_pnl_usd=total.quantize(Decimal("0.000001")),
        win_rate=win_rate,
        max_drawdown_pct=max_dd,
        positions=positions,
    )


def _infer_bar_seconds(bars: list[MarketBar]) -> int:
    """Guess bar resolution from the interval string; falls back to 60s."""
    iv = bars[0].interval
    if iv.endswith("m"):
        return int(iv[:-1]) * 60
    if iv.endswith("h"):
        return int(iv[:-1]) * 3600
    if iv.endswith("d"):
        return int(iv[:-1]) * 86400
    return 60


def _max_drawdown_pct(curve: list[Decimal]) -> Decimal:
    if not curve:
        return Decimal("0")
    peak = curve[0]
    max_dd = Decimal("0")
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd.quantize(Decimal("0.000001"))


# ----------------------------------------------------------------- orchestrator


async def run_backtest(
    strategy: str,
    symbol: str,
    asset_class: str,
    days: int,
    params: dict[str, Any] | None = None,
) -> BacktestResult:
    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    bars = await fetch_bars(symbol, asset_class, "1m", since, until)
    p = params or {}
    if strategy == "grid":
        return grid_replay(bars, p)
    if strategy == "matrix_agent":
        # Lazy import — replayers/matrix_agent.py imports from historical
        # at top-level, so a top-level back-import would cycle.
        from backtest.replayers.matrix_agent import matrix_agent_replay
        return await matrix_agent_replay(bars, p, symbol_hint=symbol)
    if strategy == "dca":
        from backtest.replayers.dca import dca_replay
        return dca_replay(bars, p)
    raise ValueError(f"unknown strategy {strategy}")


# ----------------------------------------------------------------- CLI


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix historical backtest")
    parser.add_argument("--strategy", default="grid")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--asset-class", default="crypto")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--n-grids", type=int, default=10)
    parser.add_argument("--price-band-pct", type=float, default=0.02)
    parser.add_argument("--horizon-s", type=int, default=300)
    parser.add_argument("--positions", action="store_true", help="Include full positions list in JSON output")
    args = parser.parse_args()

    params: dict[str, Any] = {
        "n_grids": args.n_grids,
        "price_band_pct": Decimal(str(args.price_band_pct)),
        "horizon_s": args.horizon_s,
    }
    result = asyncio.run(
        run_backtest(args.strategy, args.symbol, args.asset_class, args.days, params)
    )

    payload = asdict(result)
    if not args.positions:
        payload["positions"] = f"<{len(result.positions)} positions, omit --positions to inline>"
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()

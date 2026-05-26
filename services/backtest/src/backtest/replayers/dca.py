"""Historical replayer for the DCA (Dollar Cost Averaging) strategy.

DCA semantics differ from signal-based strategies:
  - Long-only, fixed-cadence buys (e.g. one every 60 minutes).
  - Positions are NOT closed mid-replay — DCA is a hold-and-accumulate
    bet. We mark every buy to the last close at end of window to score it.
  - Each individual buy is its own BacktestPosition. n_positions therefore
    equals n_buys, which is exactly what the suggester / preview UI
    visualizes (rows in the equity curve).

Score model:
  pnl_per_buy = notional_per_buy * (last_close - opened_price) / opened_price
                — slippage at open + close (mark-to-market is treated as if
                we sold at the end with one round of exit slippage).

This is a "buy & hold many times" simulation. It is intentionally simple;
the value here is comparing DCA cadences and entry timings against the
same bar series the other replayers use, with the same slippage rules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from matrix_shared.models import MarketBar

from backtest.historical import (
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_STARTING_CAPITAL,
    BacktestPosition,
    BacktestResult,
    _apply_slippage,
    _infer_bar_seconds,
    _max_drawdown_pct,
)

DEFAULT_INTERVAL_MINUTES = 60


def dca_replay(
    bars: list[MarketBar],
    params: dict[str, Any],
    *,
    starting_capital: Decimal = DEFAULT_STARTING_CAPITAL,
    max_position_pct: Decimal = DEFAULT_MAX_POSITION_PCT,
) -> BacktestResult:
    """Walk bars; buy once every interval_minutes; mark-to-market at end.

    Slippage is applied symmetrically — opening at +bps, the implicit close
    at -bps — same as the live paper engine and the other replayers.
    """
    interval_minutes = int(params.get("interval_minutes", DEFAULT_INTERVAL_MINUTES))
    notional = (starting_capital * max_position_pct).quantize(Decimal("0.01"))

    if not bars:
        now = datetime.now(timezone.utc)
        return BacktestResult(
            strategy_id="dca",
            params={"interval_minutes": interval_minutes},
            window=(now, now),
            n_bars=0, n_predictions=0, n_positions_opened=0, n_positions_closed=0,
            avg_pnl_usd=Decimal("0"), total_pnl_usd=Decimal("0"),
            win_rate=Decimal("0"), max_drawdown_pct=Decimal("0"),
        )

    bar_seconds = _infer_bar_seconds(bars)
    # How many bars between buys. At 1m bars + 60min interval → 60.
    step_bars = max(1, (interval_minutes * 60) // bar_seconds)

    # Each open buy: (opened_ts, opened_price). All closed at the same final
    # mark-to-market price at end of bars.
    open_buys: list[tuple[datetime, Decimal]] = []
    for i in range(0, len(bars), step_bars):
        bar = bars[i]
        entry_px = _apply_slippage(bar.close, "long", opening=True)
        open_buys.append((bar.ts, entry_px))

    # Mark-to-market the cumulative book bar-by-bar so the equity curve
    # reflects unrealized P&L drift, not just the final settlement.
    # equity = starting_capital + sum_over_open_buys(notional * (mark/entry - 1)).
    # Each buy lands at index k*step_bars, so by bar j we hold the first
    # `(j // step_bars) + 1` buys (clamped to total count).
    equity_curve: list[Decimal] = [starting_capital]
    for j, bar in enumerate(bars):
        n_held = min(len(open_buys), j // step_bars + 1)
        unrealized = sum(
            (notional * (bar.close / entry_px - Decimal("1"))
             for _, entry_px in open_buys[:n_held]),
            Decimal("0"),
        )
        equity_curve.append(starting_capital + unrealized)

    # Final settlement: close every buy at last bar's close (with exit slippage).
    last_bar = bars[-1]
    exit_px = _apply_slippage(last_bar.close, "long", opening=False)
    positions: list[BacktestPosition] = []
    total_pnl = Decimal("0")
    for opened_ts, opened_price in open_buys:
        # pnl in USD = notional * (exit/entry - 1)
        pnl_pct = (exit_px - opened_price) / opened_price
        pnl = (notional * pnl_pct).quantize(Decimal("0.000001"))
        total_pnl += pnl
        positions.append(BacktestPosition(
            side="long",
            opened_ts=opened_ts,
            opened_price=opened_price,
            closed_ts=last_bar.ts,
            closed_price=exit_px,
            notional_usd=notional,
            pnl_usd=pnl,
        ))

    n = len(positions)
    wins = sum(1 for p in positions if p.pnl_usd > 0)
    avg = (total_pnl / Decimal(n)) if n else Decimal("0")
    win_rate = (Decimal(wins) / Decimal(n)) if n else Decimal("0")

    return BacktestResult(
        strategy_id="dca",
        params={
            "interval_minutes": interval_minutes,
            "step_bars": step_bars,
            "notional_per_buy": str(notional),
        },
        window=(bars[0].ts, last_bar.ts),
        n_bars=len(bars),
        n_predictions=n,
        n_positions_opened=n,
        n_positions_closed=n,
        avg_pnl_usd=avg,
        total_pnl_usd=total_pnl,
        win_rate=win_rate,
        max_drawdown_pct=_max_drawdown_pct(equity_curve),
        positions=positions,
    )

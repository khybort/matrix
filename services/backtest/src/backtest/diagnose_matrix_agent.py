"""matrix_agent diagnostic CLI — find the actual alpha.

Three diagnostics in one run:

  1. HORIZON SWEEP — fix params at the live defaults, vary horizon_s
     over {60, 120, 300, 600, 1800}. If win rate climbs with horizon,
     slippage is eating us at short horizons; widen the horizon in
     prod. If it stays flat, slippage isn't the bottleneck — signal is.

  2. THRESHOLD SWEEP — fix horizon at the best from (1), vary
     signal_threshold over {0.10, 0.18, 0.25, 0.35, 0.50}. Tighter
     threshold filters out noisy decisions; if win rate jumps at tight
     thresholds we're flagging too many low-conviction trades.

  3. SOLO-SIGNAL ATTRIBUTION — for each of the 5 signals, run a
     backtest where that signal has weight=1.0 and all others 0.0
     (signal_threshold lowered to 0.01 so any non-zero score fires).
     Output per-signal win rate, n_trades, total_pnl. Signals with
     win_rate > 50% AND n_trades > 30 are genuine alpha; the rest
     are noise dragging the blend down.

Operator workflow:
  make diagnose-matrix-agent SYMBOL=BTCUSDT DAYS=7
  → review the three tables
  → pick a tighter blend, deploy via wizard or template

Output is human-readable text by default; pass --json to get a structured
dict instead.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from loguru import logger

from backtest.historical import fetch_bars
from backtest.replayers.matrix_agent import (
    DEFAULT_SIGNAL_THRESHOLD,
    DEFAULT_WEIGHTS,
    matrix_agent_replay,
)

SIGNAL_NAMES = ("trade_flow", "funding", "oi_delta", "ob_imbalance", "news")


def _short_summary(r: dict[str, Any]) -> dict[str, Any]:
    """Pull the operator-relevant fields out of a BacktestResult dump."""
    return {
        "n": r["n_positions_closed"],
        "win_rate": (
            f"{Decimal(r['win_rate']) * 100:.2f}%"
            if r["n_positions_closed"]
            else "—"
        ),
        "total_pnl_usd": f"${Decimal(r['total_pnl_usd']):.2f}",
        "avg_pnl_usd": (
            f"${Decimal(r['avg_pnl_usd']):.4f}"
            if r["n_positions_closed"]
            else "—"
        ),
        "max_dd_pct": f"{Decimal(r['max_drawdown_pct']) * 100:.2f}%",
    }


async def _one_run(bars, params: dict[str, Any], symbol: str) -> dict[str, Any]:
    result = await matrix_agent_replay(bars, params, symbol_hint=symbol)
    return asdict(result)


async def horizon_sweep(bars, symbol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for h in (60, 120, 300, 600, 1800):
        params = {
            "weights": {k: str(v) for k, v in DEFAULT_WEIGHTS.items()},
            "signal_threshold": str(DEFAULT_SIGNAL_THRESHOLD),
            "horizon_s": h,
        }
        r = await _one_run(bars, params, symbol)
        rows.append({"horizon_s": h, **_short_summary(r)})
    return rows


async def threshold_sweep(
    bars, symbol: str, *, horizon_s: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in ("0.10", "0.18", "0.25", "0.35", "0.50"):
        params = {
            "weights": {k: str(v) for k, v in DEFAULT_WEIGHTS.items()},
            "signal_threshold": t,
            "horizon_s": horizon_s,
        }
        r = await _one_run(bars, params, symbol)
        rows.append({"signal_threshold": t, **_short_summary(r)})
    return rows


async def tp_sl_sweep(
    bars, symbol: str, *, horizon_s: int
) -> list[dict[str, Any]]:
    """Fix weights + threshold + horizon; vary the asymmetric exit pair.
    Tests the diagnosis-driven hypothesis: 'losers are bigger than winners,
    so cut them faster.' Symmetric and asymmetric (tighter SL than TP)
    variants both included so we can see whether the kill-losers-fast
    intuition pays — symmetric won't separate losers from winners; it'll
    only cut tails.
    """
    base = {
        "weights": {k: str(v) for k, v in DEFAULT_WEIGHTS.items()},
        "signal_threshold": str(DEFAULT_SIGNAL_THRESHOLD),
        "horizon_s": horizon_s,
    }
    # Values calibrated for 1-min BTC bars over a 30-min horizon: at that
    # cadence, intra-position moves rarely exceed ±0.5%, so triggers above
    # 1% almost never fire. Tighter is more informative.
    cases: list[tuple[str, dict[str, Any]]] = [
        ("none",            {**base}),
        ("tp0.3/sl0.1",     {**base, "tp_pct": "0.003", "sl_pct": "0.001"}),
        ("tp0.5/sl0.2",     {**base, "tp_pct": "0.005", "sl_pct": "0.002"}),
        ("tp0.5/sl0.3",     {**base, "tp_pct": "0.005", "sl_pct": "0.003"}),
        ("tp1/sl0.3",       {**base, "tp_pct": "0.01",  "sl_pct": "0.003"}),
        ("tp_only/0.5",     {**base, "tp_pct": "0.005"}),
        ("sl_only/0.3",     {**base, "sl_pct": "0.003"}),
    ]
    rows: list[dict[str, Any]] = []
    for label, params in cases:
        r = await _one_run(bars, params, symbol)
        rows.append({"tp/sl": label, **_short_summary(r)})
    return rows


async def signal_attribution(
    bars, symbol: str, *, horizon_s: int
) -> list[dict[str, Any]]:
    """Each signal runs solo with weight=1.0 + a tiny threshold so any
    non-zero contribution opens a trade. This isolates per-signal predictive
    power, free of weight-blend interaction effects."""
    rows: list[dict[str, Any]] = []
    for sig in SIGNAL_NAMES:
        weights = {name: "0" for name in SIGNAL_NAMES}
        weights[sig] = "1.0"
        params = {
            "weights": weights,
            "signal_threshold": "0.01",
            "horizon_s": horizon_s,
        }
        r = await _one_run(bars, params, symbol)
        rows.append({"signal": sig, **_short_summary(r)})
    return rows


def _print_table(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max(len(str(r[c])) for r in rows)) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def _best_horizon(rows: list[dict[str, Any]]) -> int:
    """Pick the horizon whose total_pnl is highest (or least negative).
    Falls back to 300s if everything is identical."""
    if not rows:
        return 300
    def pnl(r: dict[str, Any]) -> Decimal:
        return Decimal(r["total_pnl_usd"].lstrip("$"))
    best = max(rows, key=pnl)
    return int(best["horizon_s"])


def _verdict(attrib: list[dict[str, Any]]) -> str:
    """Single-paragraph operator summary of the attribution table."""
    parts: list[str] = []
    for r in attrib:
        n = int(r["n"])
        if n == 0:
            parts.append(f"  {r['signal']}: never fired")
            continue
        wr_str = r["win_rate"].rstrip("%")
        wr = Decimal(wr_str) if wr_str != "—" else Decimal(0)
        pnl = Decimal(r["total_pnl_usd"].lstrip("$"))
        label = "ALPHA" if wr > 50 and n >= 30 else ("noise" if wr < 45 else "marginal")
        parts.append(
            f"  {r['signal']}: n={n} win={r['win_rate']} pnl={r['total_pnl_usd']} → {label}"
        )
    return "\n".join(parts)


async def run(symbol: str, asset_class: str, days: int, output_json: bool) -> None:
    from datetime import UTC, datetime, timedelta

    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    logger.info(f"fetching {days}d of {symbol} bars…")
    bars = await fetch_bars(symbol, asset_class, "1m", since, until)
    logger.info(f"loaded {len(bars)} bars")

    if not bars:
        print(json.dumps({"error": "no bars in window"}) if output_json else "no bars in window")
        return

    h_rows = await horizon_sweep(bars, symbol)
    best_h = _best_horizon(h_rows)
    logger.info(f"horizon sweep best (by pnl): {best_h}s")
    t_rows = await threshold_sweep(bars, symbol, horizon_s=best_h)
    a_rows = await signal_attribution(bars, symbol, horizon_s=best_h)
    # TP/SL needs intra-position bar movement to trigger. At ≤ 120s horizon
    # the one-or-two bars between open and close rarely cross 0.5% — sweep
    # would just report "no difference." Pin TP/SL sweep at 1800s where
    # the position holds long enough for asymmetric exits to actually fire.
    TPSL_HORIZON_S = 1800
    tpsl_rows = await tp_sl_sweep(bars, symbol, horizon_s=TPSL_HORIZON_S)

    if output_json:
        print(json.dumps({
            "symbol": symbol, "asset_class": asset_class, "days": days,
            "best_horizon_s": best_h,
            "horizon_sweep": h_rows,
            "threshold_sweep": t_rows,
            "signal_attribution": a_rows,
            "tp_sl_sweep": tpsl_rows,
            "verdict": _verdict(a_rows),
        }, indent=2))
        return

    _print_table("HORIZON SWEEP (default weights, default threshold)", h_rows)
    print(f"\n→ best horizon by pnl: {best_h}s")
    _print_table(f"THRESHOLD SWEEP (horizon={best_h}s)", t_rows)
    _print_table(f"SIGNAL ATTRIBUTION (solo weights, threshold=0.01, horizon={best_h}s)", a_rows)
    _print_table(f"TP/SL SWEEP (default weights/threshold, horizon={TPSL_HORIZON_S}s — pinned long so exits can fire)", tpsl_rows)
    print("\n=== verdict ===")
    print(_verdict(a_rows))


def main() -> None:
    parser = argparse.ArgumentParser(description="matrix_agent diagnostic sweeps")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--asset-class", default="crypto")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json", action="store_true", help="Structured output")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    asyncio.run(run(args.symbol, args.asset_class, args.days, args.json))


if __name__ == "__main__":
    main()

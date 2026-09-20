"""Are the take-profit and stop-loss barriers reachable at all?

57% of this book's exits are horizon exits averaging roughly minus the round
trip (docs/wiki/pnl-reality.md). That is the signature of barriers set in fixed
percentages while volatility moves underneath them: in a quiet regime a 1% take
profit over a 10-minute horizon is simply out of reach, so the trade degenerates
into "pay the spread and exit at the mark".

The standard fix is López de Prado's triple barrier with **volatility-scaled**
horizontal barriers: instead of a constant 1%, set them at m·σ_h, where σ_h is
the realised volatility of the symbol over the horizon. A label then means the
same thing in every regime.

This module measures, from the system's own history:
  1. where the current barriers sit in σ_h units (reachability), and
  2. what multiple m would have maximised net expected value,
by replaying every past signal through `edge_study.simulate_bracket` with
swapped barriers. Same signals, same exits, same costs — only the barrier
geometry changes.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from loguru import logger

from matrix_shared.edge_study import (
    Bar,
    _index_at,
    _load_bars,
    _load_candidates,
    simulate_bracket,
    subsample,
)
from matrix_shared.trading import execution_cost_bps

VOL_LOOKBACK_BARS = int(os.environ.get("MATRIX_BARRIER_VOL_LOOKBACK", "60"))
MAX_PER_STRATEGY = int(os.environ.get("MATRIX_BARRIER_MAX_PER_STRATEGY", "600"))
DEFAULT_GRID = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
_BPS = 10_000.0


def realised_vol(bars: list[Bar], idx: int, lookback: int = VOL_LOOKBACK_BARS) -> float:
    """Per-bar volatility: standard deviation of log returns over `lookback`
    bars ending at `idx`. 0.0 when there is not enough history."""
    lo = max(1, idx - lookback + 1)
    if idx - lo < 10:
        return 0.0
    rets = []
    for i in range(lo, idx + 1):
        a, b = bars[i - 1].close, bars[i].close
        if a > 0 and b > 0:
            rets.append(math.log(b / a))
    if len(rets) < 10:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def horizon_vol(per_bar_vol: float, horizon_bars: int) -> float:
    """Diffusion scaling: σ over h bars ≈ σ_bar · √h."""
    return per_bar_vol * math.sqrt(max(1, horizon_bars))


@dataclass
class BarrierRow:
    strategy_id: str
    asset_class: str
    n: int = 0
    tp_in_sigma: list[float] = field(default_factory=list)
    current_net_bps: list[float] = field(default_factory=list)
    by_m: dict[float, list[float]] = field(default_factory=dict)
    horizon_share: dict[float, int] = field(default_factory=dict)

    def as_row(self, cost_bps: float) -> dict:
        def mean(xs: list[float]) -> float:
            return sum(xs) / len(xs) if xs else 0.0

        best_m, best_net = None, None
        for m, vals in sorted(self.by_m.items()):
            net = mean(vals) - cost_bps
            if best_net is None or net > best_net:
                best_m, best_net = m, net
        return {
            "strategy": self.strategy_id,
            "market": self.asset_class,
            "n": self.n,
            "tp_sigma": round(mean(self.tp_in_sigma), 2),
            "current_net_bps": round(mean(self.current_net_bps) - cost_bps, 1),
            "best_m": best_m,
            "best_net_bps": round(best_net, 1) if best_net is not None else 0.0,
            "best_horizon_share": round(
                self.horizon_share.get(best_m, 0) / self.n, 2
            ) if self.n and best_m is not None else 0.0,
            "by_m": {m: round(mean(v) - cost_bps, 1) for m, v in sorted(self.by_m.items())},
        }


async def run_barrier_study(
    *, days: float = 14.0, strategy_id: str | None = None, grid: tuple[float, ...] = DEFAULT_GRID
) -> list[dict]:
    """Replay every signal with barriers at m·σ_h for each m in `grid`."""
    rows_in = await _load_candidates(days, strategy_id)
    if not rows_in:
        logger.warning("barrier study: no predictions in window")
        return []
    grouped: dict[tuple[str, str], list[dict]] = {}
    for r in rows_in:
        grouped.setdefault((r["strategy_id"], r["asset_class"]), []).append(r)
    sampled = [r for g in grouped.values() for r in subsample(g, MAX_PER_STRATEGY)]

    from datetime import UTC, datetime, timedelta

    since = datetime.now(UTC) - timedelta(days=days + 1)
    by_class: dict[str, set[str]] = {}
    for t in sampled:
        by_class.setdefault(t["asset_class"], set()).add(t["symbol"])
    bars: dict[tuple[str, str], list[Bar]] = {}
    for ac, syms in by_class.items():
        for sym, b in (await _load_bars(syms, ac, since)).items():
            bars[(ac, sym)] = b

    acc: dict[tuple[str, str], BarrierRow] = {}
    for t in sampled:
        series = bars.get((t["asset_class"], t["symbol"]))
        if not series or len(series) < VOL_LOOKBACK_BARS:
            continue
        horizon_bars = max(1, int((t["horizon_seconds"] or 600) // 60))
        idx = _index_at(series, t["generated_at"])
        if idx <= 0 or idx >= len(series) - 1:
            continue
        sigma_h = horizon_vol(realised_vol(series, idx), horizon_bars)
        if sigma_h <= 0:
            continue
        tp = float(t["tp_pct"]) if t["tp_pct"] is not None else 0.01
        sl = float(t["sl_pct"]) if t["sl_pct"] is not None else 0.005
        key = (t["strategy_id"], t["asset_class"])
        row = acc.setdefault(key, BarrierRow(t["strategy_id"], t["asset_class"]))
        row.n += 1
        row.tp_in_sigma.append(tp / sigma_h)
        cur = simulate_bracket(
            series, idx, side=t["side"], tp_pct=tp, sl_pct=sl, horizon_bars=horizon_bars
        )
        row.current_net_bps.append(cur.ret_bps)
        for m in grid:
            r = simulate_bracket(
                series, idx, side=t["side"], tp_pct=m * sigma_h, sl_pct=m * sigma_h,
                horizon_bars=horizon_bars,
            )
            row.by_m.setdefault(m, []).append(r.ret_bps)
            if r.reason == "hit_horizon":
                row.horizon_share[m] = row.horizon_share.get(m, 0) + 1

    cost = float(execution_cost_bps("crypto")) * 2
    out = [r.as_row(cost) for r in acc.values() if r.n >= 30]
    out.sort(key=lambda r: r["best_net_bps"] - r["current_net_bps"], reverse=True)
    return out


def format_report(rows: list[dict], *, days: float, grid: tuple[float, ...]) -> str:
    if not rows:
        return "barrier study: no data"
    head = (
        f"Volatility-scaled barriers vs the fixed ones — last {days:g}d, "
        f"{sum(r['n'] for r in rows)} signals, net of round-trip cost\n"
        f"{'strategy':<24}{'mkt':<7}{'n':>6}{'tp/σ':>7}{'now':>8}{'best m':>8}{'best':>8}{'gain':>8}  net bps by m\n"
    )
    lines = []
    for r in rows:
        by_m = " ".join(f"{m:g}:{v:+.0f}" for m, v in r["by_m"].items())
        lines.append(
            f"{r['strategy']:<24}{r['market']:<7}{r['n']:>6}{r['tp_sigma']:>7.1f}"
            f"{r['current_net_bps']:>8.1f}{r['best_m']:>8g}{r['best_net_bps']:>8.1f}"
            f"{r['best_net_bps'] - r['current_net_bps']:>8.1f}  {by_m}"
        )
    tail = (
        "\ntp/σ is the current take-profit in units of horizon volatility: "
        "above ~1.5 the barrier is rarely reachable and the trade degenerates "
        "into a horizon exit at minus cost."
    )
    return head + "\n".join(lines) + tail

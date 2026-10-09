"""Would resting the entry order save more than it costs?

Cost is the binding constraint on this book: no strategy has a verified edge
(the ~+31 bps once quoted here was withdrawn 2026-10-09 as pseudo-replication,
docs/wiki/edge-study.md) and the taker round trip is ~15 bps. Entering passively
turns the entry leg from taker into maker — on Bybit linear perps 1 bp instead
of 5.5 bps plus ~2 bps of slippage — which is ~6.5 bps of the edge recovered
without finding any new signal.

Nothing is free: a resting order only fills when the market comes to it, which
is disproportionately when it is about to keep going against you (adverse
selection), and sometimes it never fills and the opportunity is lost. This
module measures both effects on the system's own signals:

    policy: post a limit at the signal bar's close; if the market trades
            through it within `wait_bars`, fill as maker; otherwise cross at
            market and pay taker (or skip entirely, reported separately).

Everything is replayed on 1m bars with the existing bracket simulator, so the
comparison against the current all-taker policy is like-for-like.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from loguru import logger

from matrix_shared.edge_study import (
    Bar,
    MAX_PER_STRATEGY,
    _load_bars,
    _load_candidates,
    entry_index,
    sample_episodes,
    simulate_bracket,
    welch,
)
from matrix_shared.trading import execution_cost_bps

MAKER_BPS = float(os.environ.get("MATRIX_MAKER_BPS", "1.0"))
WAIT_BARS = int(os.environ.get("MATRIX_POST_ONLY_WAIT_BARS", "1"))
_BPS = 10_000.0


@dataclass(frozen=True, slots=True)
class Entry:
    filled: bool
    idx: int
    maker: bool


def post_only_entry(bars: list[Bar], idx: int, side: str, wait_bars: int = WAIT_BARS) -> Entry:
    """Rest a limit at `bars[idx].close` and wait `wait_bars` bars for a touch.

    A buy fills when the market trades down to the limit (bar low <= limit); a
    sell when it trades up to it. Unfilled after the window, the caller either
    crosses (taker at the then-current close) or skips.
    """
    if idx < 0 or idx >= len(bars) - 1:
        return Entry(False, idx, False)
    limit = bars[idx].close
    long = side != "short"
    last = min(idx + wait_bars, len(bars) - 1)
    for i in range(idx + 1, last + 1):
        touched = bars[i].low <= limit if long else bars[i].high >= limit
        if touched:
            return Entry(True, i, True)
    return Entry(False, last, False)


@dataclass
class ExecRow:
    strategy_id: str
    asset_class: str
    n: int = 0
    n_raw: int = 0
    taker_net: list[float] = field(default_factory=list)
    maker_net: list[float] = field(default_factory=list)   # post-only, cross if unfilled
    passive_only_net: list[float] = field(default_factory=list)  # skip if unfilled
    fills: int = 0

    def as_row(self) -> dict:
        def mean(xs):
            return sum(xs) / len(xs) if xs else 0.0

        diff, se, t = welch(self.maker_net, self.taker_net)
        return {
            "strategy": self.strategy_id, "market": self.asset_class, "n": self.n,
            "n_raw": self.n_raw,
            "fill_rate": round(self.fills / self.n, 3) if self.n else 0.0,
            "taker_net_bps": round(mean(self.taker_net), 1),
            "postonly_net_bps": round(mean(self.maker_net), 1),
            "passive_only_bps": round(mean(self.passive_only_net), 1),
            "gain_bps": round(diff, 1), "t": round(t, 2),
        }


async def run_execution_study(
    *, days: float = 14.0, strategy_id: str | None = None, wait_bars: int = WAIT_BARS
) -> list[dict]:
    rows_in = await _load_candidates(days, strategy_id)
    if not rows_in:
        logger.warning("execution study: no predictions in window")
        return []
    # One bet, one sample (edge_study.one_per_episode); see docs/wiki/edge-study.md.
    sampled, n_raw = sample_episodes(rows_in, MAX_PER_STRATEGY)

    from datetime import UTC, datetime, timedelta

    since = datetime.now(UTC) - timedelta(days=days + 1)
    by_class: dict[str, set[str]] = {}
    for t in sampled:
        by_class.setdefault(t["asset_class"], set()).add(t["symbol"])
    bars: dict[tuple[str, str], list[Bar]] = {}
    for ac, syms in by_class.items():
        for sym, b in (await _load_bars(syms, ac, since)).items():
            bars[(ac, sym)] = b

    acc: dict[tuple[str, str], ExecRow] = {}
    for t in sampled:
        series = bars.get((t["asset_class"], t["symbol"]))
        if not series:
            continue
        # The limit rests at the last price the strategy had seen, not at the
        # close of a bar that was still forming when it decided.
        idx = entry_index(series, t["generated_at"])
        if idx <= 0 or idx >= len(series) - 2:
            continue
        horizon_bars = max(1, int((t["horizon_seconds"] or 600) // 60))
        tp = float(t["tp_pct"]) if t["tp_pct"] is not None else 0.01
        sl = float(t["sl_pct"]) if t["sl_pct"] is not None else 0.005
        per_side = float(execution_cost_bps(t["asset_class"], t["symbol"]))
        taker_round = per_side * 2
        maker_round = MAKER_BPS + per_side          # maker in, taker out

        base = simulate_bracket(series, idx, side=t["side"], tp_pct=tp, sl_pct=sl,
                                horizon_bars=horizon_bars)
        if base.reason == "no_data":
            continue
        key = (t["strategy_id"], t["asset_class"])
        row = acc.setdefault(
            key, ExecRow(t["strategy_id"], t["asset_class"], n_raw=n_raw.get(key, 0))
        )
        row.n += 1
        row.taker_net.append(base.ret_bps - taker_round)

        e = post_only_entry(series, idx, t["side"], wait_bars)
        if e.filled:
            row.fills += 1
            # The bracket starts from the fill bar; the remaining horizon is
            # shorter by the bars spent waiting.
            left = max(1, horizon_bars - (e.idx - idx))
            r = simulate_bracket(series, e.idx, side=t["side"], tp_pct=tp, sl_pct=sl,
                                 horizon_bars=left)
            net = r.ret_bps - maker_round if r.reason != "no_data" else 0.0
            row.maker_net.append(net)
            row.passive_only_net.append(net)
        else:
            # crossed after the wait: taker cost, entry at the later close
            left = max(1, horizon_bars - (e.idx - idx))
            r = simulate_bracket(series, e.idx, side=t["side"], tp_pct=tp, sl_pct=sl,
                                 horizon_bars=left)
            row.maker_net.append(r.ret_bps - taker_round if r.reason != "no_data" else 0.0)
            row.passive_only_net.append(0.0)   # opportunity skipped, no PnL

    out = [r.as_row() for r in acc.values() if r.n >= 30]
    out.sort(key=lambda r: r["gain_bps"], reverse=True)
    return out


def format_report(rows: list[dict], *, days: float, wait_bars: int) -> str:
    if not rows:
        return "execution study: no data"
    head = (
        f"Post-only entry vs crossing — last {days:g}d, {wait_bars} bar wait, "
        f"maker {MAKER_BPS:g} bps\n"
        f"{'strategy':<24}{'mkt':<7}{'n':>6}{'fill':>7}{'taker':>9}{'postonly':>10}"
        f"{'passive':>9}{'gain':>7}{'t':>7}\n"
    )
    lines = [
        f"{r['strategy']:<24}{r['market']:<7}{r['n']:>6}{r['fill_rate']:>7.2f}"
        f"{r['taker_net_bps']:>9.1f}{r['postonly_net_bps']:>10.1f}"
        f"{r['passive_only_bps']:>9.1f}{r['gain_bps']:>7.1f}{r['t']:>7.2f}"
        for r in rows
    ]
    return head + "\n".join(lines) + (
        "\ntaker = today's policy (cross now). postonly = rest, cross if unfilled. "
        "passive = rest, skip if unfilled. All net of fees; gain is postonly − taker."
    )

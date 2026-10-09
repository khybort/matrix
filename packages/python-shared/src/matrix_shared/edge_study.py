"""Does any strategy time its entries better than chance?

The book loses ~18 bps a trade (docs/wiki/pnl-reality.md). Tuning only helps if
the entries carry signal at all, so this module answers the prior question with
a controlled experiment rather than another aggregate.

Design — one simulator, three arms:

  treatment    : the strategy's real entry bar and real side
  control-time : K random entry bars, SAME side, barriers and horizon
                 → tests whether the strategy times its entries
  control-side : the SAME entry bar, random side
                 → tests whether the strategy picks its direction

Both nulls are needed and they answer different questions. A strategy can time
entries well while having no directional view (it would beat control-time and
lose to control-side), or pick direction well at arbitrary times (the reverse).
Judging on one null alone — as this module did until 2026-09-20 — risks
deleting a strategy that has the other kind of edge.

Both are replayed on 1m bars by `simulate_bracket`, so fees, slippage, exit
rules, holding time and symbol mix are identical by construction and the only
difference left is *when* the trade was opened. A strategy with real edge beats
its control; one that does not is paying spread for noise.

Returns are gross (no costs): the cost is a known constant
(`trading.execution_cost_bps` × 2) and adding it to both arms would only shift
both means. The report prints it so net can be read off.

Entry rule (2026-10-09): every arm enters at the OPEN of the first 1m bar that
starts at or after `generated_at + ENTRY_LATENCY` and is scored from that bar
on. No part of the scored path precedes the signal. Until then the treatment
entered at the close of the last bar closed before the signal and was scored
from the bar in force, so up to a minute of pre-signal price path — the move
that triggered a momentum or breakout signal — was credited to the strategy.

Carries (CARRY_SIDES) are not brackets and are never simulated. Their evidence
is the realised net of their closed paper episodes (`carry_edge_rows`), tested
against zero with a day-clustered t and put through the same BHY / deflated
Sharpe / pre-registered-n bar, so `status` means the same thing for both.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pathlib import Path
from typing import Any

from loguru import logger

from matrix_shared.model_store import model_path
from matrix_shared.promotion import Registry, deflated_sharpe
from matrix_shared.promotion import status as promotion_status
from sqlalchemy import text

from matrix_shared.db import local_session_scope, shared_session_scope
from matrix_shared.trading import CARRY_SIDES

CONTROL_DRAWS = int(os.environ.get("MATRIX_EDGE_CONTROL_DRAWS", "20"))
# Evaluate signals, not fills. Only ~12% of predictions ever become positions
# (slots, cash, backpressure), so judging a strategy by its fills throws away
# seven eighths of the evidence it produced — and a demoted strategy at zero
# slots would never generate any evidence again. The simulator does not care
# whether capital was committed, so every prediction is replayed.
# Signals per strategy the study will simulate. This is a power limit, not a
# performance knob: the pre-registered stopping rule asks whether a strategy has
# accumulated enough evidence, and a cap below its registered target makes that
# target unreachable by construction. On 2026-09-20 momentum_xs registered 936
# against a cap of 800 and would have sat at `provisional` forever. Keep this
# comfortably above the largest registered `required_n`.
MAX_PER_STRATEGY = int(os.environ.get("MATRIX_EDGE_MAX_PER_STRATEGY", "1500"))
MIN_TRADES = int(os.environ.get("MATRIX_EDGE_MIN_TRADES", "30"))
# Bar-freshness guards (2026-10-09). The entry bar's close may be at most
# MAX_ENTRY_AGE old at signal time, and no two consecutive bars inside a
# simulated window may be further apart than MAX_BAR_GAP; otherwise the
# signal (or control draw) is unscorable, never scored across the hole.
_BAR = timedelta(minutes=1)
MAX_ENTRY_AGE = timedelta(minutes=float(os.environ.get("MATRIX_EDGE_MAX_ENTRY_AGE_MIN", "3")))
MAX_BAR_GAP = timedelta(minutes=float(os.environ.get("MATRIX_EDGE_MAX_BAR_GAP_MIN", "3")))
# Entry latency: the paper engine fills ~4 s after a signal (measured on the
# momentum_xs 2026-09-21 decomposition, docs/wiki/edge-study.md). The earliest
# price any arm may enter at is the open of the first bar starting at or after
# generated_at + this.
ENTRY_LATENCY = timedelta(seconds=float(os.environ.get("MATRIX_EDGE_ENTRY_LATENCY_S", "4")))
# What one sample is. Rows cached or registered under any other unit were
# measured on re-emitted signals counted as independent trades and are void.
SAMPLE_UNIT = "episode"
DEFAULT_TP_PCT = 0.01
DEFAULT_SL_PCT = 0.005
_BPS = 10_000.0


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime
    high: float
    low: float
    close: float
    # First trade of the bar. None only in synthetic series; the entry then
    # falls back to the bar's close (see `entry_price`).
    open: float | None = None


@dataclass(frozen=True, slots=True)
class SimResult:
    reason: str  # hit_tp | hit_sl | hit_horizon | no_data
    ret_bps: float
    bars_held: int


def entry_price(bar: Bar) -> float:
    """The price a trade entering on `bar` gets: its open (first trade at or
    after the bar's start). Synthetic bars without an open use the close."""
    return bar.open if bar.open is not None else bar.close


def simulate_bracket(
    bars: list[Bar], entry_idx: int, *, side: str, tp_pct: float, sl_pct: float, horizon_bars: int,
    entry_px: float | None = None,
) -> SimResult:
    """Replay one bracketed trade on 1m bars entering at the OPEN of
    `bars[entry_idx]` (or `entry_px`, e.g. a resting limit's price) and holding
    `horizon_bars` bars: the entry bar itself and the ones after it, exiting at
    the close of the last. Every bar scored starts at or after the entry.

    Take-profit wins ties within a bar, matching the paper engine. Returns the
    gross return in bps, signed for the side.
    """
    if entry_idx < 0 or entry_idx >= len(bars) or horizon_bars <= 0:
        return SimResult("no_data", 0.0, 0)
    entry = entry_px if entry_px is not None else entry_price(bars[entry_idx])
    if entry <= 0:
        return SimResult("no_data", 0.0, 0)
    long = side != "short"
    tp_px = entry * (1 + tp_pct) if long else entry * (1 - tp_pct)
    sl_px = entry * (1 - sl_pct) if long else entry * (1 + sl_pct)
    last = min(entry_idx + horizon_bars - 1, len(bars) - 1)
    for i in range(entry_idx, last + 1):
        b = bars[i]
        if i > entry_idx and b.ts - bars[i - 1].ts > MAX_BAR_GAP:
            # A hole in the series: what follows is not reachable by holding.
            return SimResult("no_data", 0.0, 0)
        hit_tp = b.high >= tp_px if long else b.low <= tp_px
        hit_sl = b.low <= sl_px if long else b.high >= sl_px
        if hit_tp:
            return SimResult("hit_tp", tp_pct * _BPS, i - entry_idx + 1)
        if hit_sl:
            return SimResult("hit_sl", -sl_pct * _BPS, i - entry_idx + 1)
    exit_px = bars[last].close
    raw = (exit_px - entry) / entry
    return SimResult("hit_horizon", (raw if long else -raw) * _BPS, last - entry_idx + 1)


def _norm_sf(t: float) -> float:
    """Upper-tail probability of the standard normal (two-sided p = 2·sf(|t|)).
    n is in the hundreds here, so the normal approximation to Student's t is
    accurate and avoids a scipy dependency."""
    return 0.5 * math.erfc(abs(t) / math.sqrt(2.0))


def two_sided_p(t: float) -> float:
    return min(1.0, 2.0 * _norm_sf(t))


def benjamini_hochberg(pvalues: list[float], q: float = 0.05) -> list[bool]:
    """Which hypotheses survive at false-discovery rate `q`.

    Thirteen strategies are compared at once, so an uncorrected 5% threshold
    expects roughly one false discovery every run — exactly the mechanism
    behind published backtests that never repeat (Bailey & López de Prado,
    "The Deflated Sharpe Ratio"). BH controls the expected share of false
    positives among the ones we act on, which is the quantity that matters
    when the action is "allocate capital".
    """
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    keep = [False] * n
    cutoff = -1
    for rank, i in enumerate(order, start=1):
        if pvalues[i] <= q * rank / n:
            cutoff = rank
    for rank, i in enumerate(order, start=1):
        if rank <= cutoff:
            keep[i] = True
    return keep


def benjamini_yekutieli(pvalues: list[float], q: float = 0.05) -> list[bool]:
    """BH's dependency-safe sibling: same procedure, threshold divided by H(m).

    BH controls the false-discovery rate only when the tests are independent or
    positively dependent. Ours are neither: every strategy is scored on the same
    bars, the same symbols and overlapping windows, so a quiet market makes all
    thirteen look alike at once. Benjamini-Yekutieli divides the threshold by
    the harmonic number H(m) = sum(1/i), which is valid under *arbitrary*
    dependence and costs a factor of about 3.2 at m=13.

    That factor is the price of an honest promotion bar, and it is worth paying
    precisely because the decision downstream is "give this strategy capital".
    """
    n = len(pvalues)
    if n == 0:
        return []
    harmonic = sum(1.0 / i for i in range(1, n + 1))
    return benjamini_hochberg(pvalues, q / harmonic)


def welch(a: list[float], b: list[float]) -> tuple[float, float, float]:
    """(mean difference a−b, standard error, t statistic) for unequal variances."""
    if len(a) < 2 or len(b) < 2:
        return (0.0, 0.0, 0.0)
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    if se == 0:
        return (ma - mb, 0.0, 0.0)
    return (ma - mb, se, (ma - mb) / se)


@dataclass
class StrategyEdge:
    strategy_id: str
    asset_class: str
    n: int = 0
    n_raw: int = 0  # signal rows before collapsing re-emissions into episodes
    n_unscorable: int = 0  # episodes dropped: stale entry bar or a hole in the window
    n_filled: int = 0
    treatment: list[float] = field(default_factory=list)
    control: list[float] = field(default_factory=list)
    control_side: list[float] = field(default_factory=list)
    realised_net_bps: float = 0.0
    tp_rate: float = 0.0

    def as_row(self) -> dict:
        t_mean = sum(self.treatment) / len(self.treatment) if self.treatment else 0.0
        t_sd = (
            math.sqrt(sum((x - t_mean) ** 2 for x in self.treatment) / (len(self.treatment) - 1))
            if len(self.treatment) > 1
            else 0.0
        )
        c_mean = sum(self.control) / len(self.control) if self.control else 0.0
        s_mean = sum(self.control_side) / len(self.control_side) if self.control_side else 0.0
        diff, se, t = welch(self.treatment, self.control)
        diff_s, se_s, t_s = welch(self.treatment, self.control_side)
        return {
            "strategy": self.strategy_id,
            "market": self.asset_class,
            "n": self.n,
            "n_raw": self.n_raw,
            "n_unscorable": self.n_unscorable,
            "unit": SAMPLE_UNIT,
            "n_filled": self.n_filled,
            "gross_bps": round(t_mean, 2),
            "control_bps": round(c_mean, 2),
            "edge_bps": round(diff, 2),
            "t": round(t, 2),
            "side_edge_bps": round(diff_s, 2),
            "t_side": round(t_s, 2),
            "p": round(two_sided_p(t), 5),
            # provisional; run_edge_study replaces it with the FDR-corrected call
            "significant": abs(t) >= 1.96 and self.n >= MIN_TRADES,
            "control_side_bps": round(s_mean, 2),
            "realised_net_bps": round(self.realised_net_bps, 2),
            "sim_tp_rate": round(self.tp_rate, 3),
            # Per-trade dispersion of the treatment arm, in bps. Kelly sizing
            # needs the variance, not only the mean (see allocation.kelly_*).
            "sd_bps": round(t_sd, 2),
        }


async def _load_candidates(days: float, strategy_id: str | None) -> list[dict]:
    """Every non-hold prediction in the window, filled or not, with its realised
    outcome when there was one."""
    sql = (
        "SELECT p.strategy_id, p.asset_class, p.symbol, p.side, p.horizon_seconds, "
        "       p.tp_pct, p.sl_pct, p.generated_at, "
        "       (pp.id IS NOT NULL) AS filled, o.pnl_pct "
        "FROM predictions p "
        "LEFT JOIN paper_positions pp ON pp.prediction_id = p.id "
        "LEFT JOIN outcomes o ON o.prediction_id = p.id AND o.reason <> 'orphan_flat_close' "
        "WHERE p.generated_at >= now() - make_interval(secs => :secs) "
        "  AND p.side IN ('long','short') "
        "  AND coalesce(p.context->>'is_shadow','false') = 'false' "
        # Exploration probes are not the strategy's policy (74% of matrix_agent's
        # rows over 40 days): counting them measures the probe budget, not the edge.
        "  AND coalesce(p.context->>'is_exploration','false') = 'false' "
        + ("  AND p.strategy_id = :sid " if strategy_id else "")
        + "ORDER BY p.generated_at"
    )
    params = {"secs": days * 86400}
    if strategy_id:
        params["sid"] = strategy_id
    async with shared_session_scope() as s:
        rows = (await s.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


def episode_groups(items: list[dict]) -> list[list[dict]]:
    """Group signals into episodes: one bet and its re-emissions.

    A signal opens a new episode only when no earlier episode of the same
    (strategy, market, symbol, side) is still inside the horizon of the signal
    that opened it; otherwise it joins that episode. `items` must be ordered
    by `generated_at` and carry strategy_id, asset_class, symbol, side,
    generated_at and horizon_seconds. The first member of each group is the
    bet; the rest are the same call made again.

    Every consumer that treats signals or fills as samples goes through here
    (edge, barrier, horizon, execution and meta-label studies; the paper
    certificate, efficacy and the slot scorer), so "one bet, one sample" has
    exactly one definition.
    """
    open_until: dict[tuple, datetime] = {}
    current: dict[tuple, list[dict]] = {}
    out: list[list[dict]] = []
    for r in items:
        key = (r["strategy_id"], r["asset_class"], r["symbol"], r["side"])
        at = r["generated_at"]
        until = open_until.get(key)
        if until is not None and at < until:
            current[key].append(r)
            continue
        open_until[key] = at + timedelta(seconds=int(r["horizon_seconds"] or 600))
        group = [r]
        current[key] = group
        out.append(group)
    return out


def one_per_episode(items: list[dict]) -> list[dict]:
    """Collapse re-emissions of one bet into the bet: keep a signal only when no
    earlier kept signal of the same (strategy, market, symbol, side) is still
    inside its horizon. `items` must be ordered by `generated_at`.

    The unit of evidence is an independent bet, not a row. On 2026-09-13
    momentum_xs v1 re-emitted the same ten (symbol, side) pairs every ~90 s for
    three hours — 1 764 rows, UAIUSDT short alone 294 times. Those rows were
    87 % of the 799 signals behind "+36 bps, t=6"; the wallet can hold each bet
    once, and without them the strategy measured -18 bps against random entry
    (docs/wiki/edge-study.md). Counting duplicates as samples multiplies one
    afternoon's luck into a t-statistic.
    """
    return [g[0] for g in episode_groups(items)]


def episode_pnls(items: list[dict], *, value: str = "pnl_usd") -> list[float]:
    """Realised results summed per episode, in episode order.

    For fills the duplicates were real positions — the wallet did hold the same
    bet two or three times — so their dollars are not dropped, they are added
    into the one draw they jointly were. funding_reversion held 2 016 of its
    3 101 closed positions (2026-09) while an identical (symbol, side) position
    of its own was already open; scored per row, one wrong call counted as
    three independent losses and a lucky one as three wins.
    """
    return [sum(float(r[value] or 0) for r in g) for g in episode_groups(items)]


def episode_summary(
    rows: list[dict], key: str | Callable[[dict], Any], *, value: str = "pnl_usd"
) -> dict[Any, dict[str, float]]:
    """Per-key {n, n_raw, wins, sum} with one sample per episode.

    `key` is a field name or a function of the episode's first row (the bet).
    n counts episodes, n_raw rows; an episode wins when its summed `value` is
    positive; `sum` keeps every row's dollars. Rows need the `episode_groups`
    fields and are sorted here, so callers may pass them in any order.
    """
    get = key if callable(key) else (lambda r: r[key])
    out: dict[Any, dict[str, float]] = {}
    for g in episode_groups(sorted(rows, key=lambda r: r["generated_at"])):
        total = sum(float(r[value] or 0) for r in g)
        s = out.setdefault(get(g[0]), {"n": 0, "n_raw": 0, "wins": 0, "sum": 0.0})
        s["n"] += 1
        s["n_raw"] += len(g)
        s["wins"] += 1 if total > 0 else 0
        s["sum"] += total
    return out


def sample_episodes(rows: list[dict], cap: int) -> tuple[list[dict], dict[tuple[str, str], int]]:
    """Collapse to episodes, then subsample each (strategy, market) to `cap`.
    Returns the sampled episodes and the raw row count per (strategy, market),
    so reports can print n beside n_raw."""
    n_raw: dict[tuple[str, str], int] = {}
    for r in rows:
        k = (r["strategy_id"], r["asset_class"])
        n_raw[k] = n_raw.get(k, 0) + 1
    grouped: dict[tuple[str, str], list[dict]] = {}
    for r in one_per_episode(rows):
        grouped.setdefault((r["strategy_id"], r["asset_class"]), []).append(r)
    return [r for g in grouped.values() for r in subsample(g, cap)], n_raw


def entry_index(bars: list[Bar], generated_at: datetime) -> int:
    """The first bar that starts at or after `generated_at + ENTRY_LATENCY`;
    the trade enters at its open and is scored from it. -1 when there is none,
    or when it starts more than MAX_ENTRY_AGE later (a hole after the signal:
    the first tradeable price is not the one the strategy acted on).

    Bar `ts` is the bar's START (ingestion buckets by date_trunc('minute')).
    Until 2026-10-09 this returned the last bar CLOSED before the signal and
    the simulator scored from the next bar — the bar in force at
    `generated_at`, which began up to 60 s before it. For a momentum or
    breakout signal those seconds are the move that fired it, and they were
    credited to the strategy as return (correctness review 2026-10-09). The
    rule before that (enter at the close of the bar in force) let the
    treatment trade at a price it could not have seen. Neither is honest;
    the first price observable after the signal is.
    """
    at = generated_at + ENTRY_LATENCY
    i = _first_at_or_after(bars, at)
    # A symbol whose series has a hole after the signal (the September outage,
    # a symbol not aggregated for days) has its next bar hours later; entering
    # there scores a jump across the hole. On 2026-10-09 stale entries made
    # bist_volume_breakout +116 bps (t=10); the fresh ones were -14 bps.
    if i < 0 or bars[i].ts - at > MAX_ENTRY_AGE:
        return -1
    return i


def _first_at_or_after(bars: list[Bar], when: datetime) -> int:
    lo, hi = 0, len(bars)
    while lo < hi:
        mid = (lo + hi) // 2
        if bars[mid].ts < when:
            lo = mid + 1
        else:
            hi = mid
    return lo if lo < len(bars) else -1


def contiguous(bars: list[Bar], start: int, end: int) -> bool:
    """No hole wider than MAX_BAR_GAP between consecutive bars in [start, end].
    A window with a hole is not `end - start` minutes of market; it is a jump
    the simulator would score as if it had been tradeable."""
    for i in range(max(start, 0) + 1, min(end, len(bars) - 1) + 1):
        if bars[i].ts - bars[i - 1].ts > MAX_BAR_GAP:
            return False
    return True


def subsample(items: list[dict], cap: int) -> list[dict]:
    """Evenly spaced subsample, preserving order — keeps the whole window
    represented instead of only its first hours."""
    if cap <= 0 or len(items) <= cap:
        return items
    step = len(items) / cap
    return [items[int(i * step)] for i in range(cap)]


async def _load_bars(symbols: set[str], asset_class: str, since: datetime) -> dict[str, list[Bar]]:
    if not symbols:
        return {}
    async with local_session_scope() as s:
        rows = (await s.execute(text(
            "SELECT symbol, ts, high, low, close, open FROM market_bars "
            "WHERE asset_class = :ac AND interval = '1m' AND ts >= :since "
            "AND symbol = ANY(:syms) ORDER BY symbol, ts"
        ), {"ac": asset_class, "since": since, "syms": list(symbols)})).all()
    out: dict[str, list[Bar]] = {}
    for sym, ts, high, low, close, open_ in rows:
        out.setdefault(sym, []).append(Bar(
            ts if ts.tzinfo else ts.replace(tzinfo=UTC), float(high), float(low), float(close),
            float(open_) if open_ is not None else None,
        ))
    return out


def _index_at(bars: list[Bar], when: datetime) -> int:
    lo, hi = 0, len(bars) - 1
    if hi < 0 or when < bars[0].ts:
        return -1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if bars[mid].ts <= when:
            lo = mid
        else:
            hi = mid - 1
    return lo


async def _family_size(days: float) -> int:
    """How many (strategy, market) hypotheses the book tests in this window:
    every pair that emitted a directional signal, plus every carry pair with a
    closed paper episode in the carry window (they share the promotion bar)."""
    async with shared_session_scope() as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM ("
            " SELECT strategy_id, asset_class FROM predictions "
            " WHERE generated_at >= now() - make_interval(secs => :secs) "
            " AND side IN ('long','short') "
            " AND coalesce(context->>'is_shadow','false') = 'false' "
            " UNION "
            " SELECT p.strategy_id, p.asset_class FROM paper_positions pp "
            " JOIN predictions p ON p.id = pp.prediction_id "
            " WHERE p.generated_at >= now() - make_interval(secs => :csecs) "
            " AND p.side = ANY(:carry) AND pp.closed_at IS NOT NULL"
            ") f"
        ), {"secs": days * 86400, "csecs": CARRY_DAYS * 86400, "carry": sorted(CARRY_SIDES)})).scalar()
    return int(n or 1)


# ---------------------------------------------------------------- carries
#
# A carry (CARRY_SIDES: hedged funding capture) has no entry-time question for
# a bracket replay to answer: its result is funding received minus four taker
# fees, the two books walked at open and close, and borrow on the short spot
# leg — none of which a 1m-bar bracket models. Until 2026-10-09
# `_load_candidates` read only long/short, so a carry could never reach
# `confirmed`: `paper_trade._promotion_confirmed` was always False for it, the
# book-priced carry stayed at its $500/leg ceiling and Kelly never applied.
#
# Evidence for a carry is therefore REALISED, never simulated: the net bps of
# each closed paper episode (pnl_usd is already net of funding, fees, book
# cost and borrow; shadow_tracker.decompose splits it), tested against zero
# with a day-clustered t — carries opened the same day share one funding
# regime and are not independent bets — and pushed through the same BHY,
# deflated Sharpe and pre-registered n as every directional row.

# Carry episodes are sparse (neg_funding_carry ~50 a week) and the
# pre-registered floor is 200, so a 14-day window could never reach it.
CARRY_DAYS = float(os.environ.get("MATRIX_EDGE_CARRY_DAYS", "90"))
# Day clusters required before a carry's t is read at all: with few clusters
# the cluster-robust SE is itself noise and the normal approximation flatters.
CARRY_MIN_DAYS = int(os.environ.get("MATRIX_EDGE_CARRY_MIN_DAYS", "20"))
# Carry closes booked before this instant are not evidence. Until the
# per-settlement funding accounting went live (backtest.carry_funding, wired
# into the paper engine at 7d7b854, 2026-10-09 13:39:14 UTC) a carry booked
# `hours/8 x rate_at_close` and exited on Bybit's post-settlement placeholder:
# 51 of 51 inverse/xexch carries "flipped" at their first settlement and closed
# at the four-leg fee (~ -24 bps) with no funding. Those episodes measure the
# bug, not the strategy. A strategy's own band `since`, when later, wins.
# Matched on closed_at: an episode opened before and closed after the fix is
# booked under the new accounting end to end.
CARRY_EVIDENCE_SINCE = datetime.fromisoformat(
    os.environ.get("MATRIX_EDGE_CARRY_EVIDENCE_SINCE") or "2026-10-09T13:39:14+00:00"
)

_CARRY_FILLS_SQL = (
    "SELECT p.strategy_id, p.asset_class, p.symbol, p.side, p.generated_at, p.horizon_seconds, "
    "       pp.notional_usd, pp.opened_at, pp.closed_at, pp.pnl_usd, "
    "       p.context->>'borrow_rate_hourly' AS borrow_rate_hourly, "
    "       p.context->>'borrow_charged_usd' AS borrow_charged_usd, "
    "       p.context->'book_close'->>'total_bps' AS book_close_bps, "
    "       coalesce(p.context->>'is_shadow','false') = 'true' AS is_shadow, "
    "       p.context->'exec_precheck'->>'status' AS exec_precheck "
    "FROM paper_positions pp JOIN predictions p ON p.id = pp.prediction_id "
    "WHERE p.side = ANY(:carry) "
    "  AND p.generated_at >= now() - make_interval(secs => :secs) "
    "  AND coalesce(p.context->>'is_exploration','false') = 'false' "
)


async def _load_carry_fills(days: float, strategy_id: str | None) -> list[dict]:
    """Every carry paper position (open or closed) in the window, with what
    `shadow_tracker.decompose` needs. Shadow-wallet fills are included: a
    carry's paper book IS its evidence (neg_funding_carry trades only in the
    shadow wallet), unlike a directional signal, which is replayed whether or
    not it was filled. Positions closed before CARRY_EVIDENCE_SINCE (the old
    funding accounting) and episodes before a strategy's registered shadow
    band `since` (its current accounting) are dropped."""
    sql = _CARRY_FILLS_SQL + ("  AND p.strategy_id = :sid " if strategy_id else "") + "ORDER BY p.generated_at"
    params: dict[str, Any] = {"carry": sorted(CARRY_SIDES), "secs": days * 86400}
    if strategy_id:
        params["sid"] = strategy_id
    async with shared_session_scope() as s:
        rows = [dict(r) for r in (await s.execute(text(sql), params)).mappings().all()]
    cutoff = CARRY_EVIDENCE_SINCE if CARRY_EVIDENCE_SINCE.tzinfo else CARRY_EVIDENCE_SINCE.replace(tzinfo=UTC)
    rows = [r for r in rows if r["closed_at"] is None or _aware(r["closed_at"]) >= cutoff]
    if not rows:
        return rows
    try:
        from matrix_shared.shadow_tracker import load_bands

        bands = await load_bands()
    except Exception as e:  # noqa: BLE001 — a missing band only widens the window
        logger.debug(f"carry evidence: shadow bands unavailable ({e})")
        bands = {}
    since: dict[tuple[str, str], datetime] = {}
    for key, band in bands.items():
        if band.get("since"):
            ts = datetime.fromisoformat(band["since"])
            since[key] = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return [
        r for r in rows
        if (k := (r["strategy_id"], r["asset_class"])) not in since or _aware(r["generated_at"]) >= since[k]
    ]


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def carry_edge_rows(fills: list[dict]) -> tuple[list[dict], dict[tuple[str, str], list[float]]]:
    """One evidence row per carry (strategy, market) from its CLOSED paper
    episodes, shaped like a directional row so `verdict`, the promotion bar
    and every reader treat it alike. Returns (rows, per-episode net bps by key).

    The null is zero: a carry is paid its realised net, not its lead over a
    control, so `edge_bps` = `gross_bps` = mean net per episode and
    `net_of_costs` tells `verdict` not to charge the round trip again. Open
    episodes are not evidence yet (`n_open`). The t is clustered by the UTC day
    the episode opened (`t_day`); with fewer than CARRY_MIN_DAYS clusters `t`
    is 0 and p is 1.

    Executable episodes only: a position the live executor would have aborted
    at open (`exec_precheck = would_abort`) cannot confirm a strategy whose
    capital goes through that executor (`n_would_abort`).
    """
    from matrix_shared.shadow_tracker import clustered_t, decompose, executable

    by_key: dict[tuple[str, str], list[dict]] = {}
    for r in fills:
        by_key.setdefault((r["strategy_id"], r["asset_class"]), []).append(r)
    rows: list[dict] = []
    returns: dict[tuple[str, str], list[float]] = {}
    for (sid, ac), rs in by_key.items():
        # One arm per row. A strategy with an active config and a `shadow`
        # challenger trades both under one strategy_id with different params
        # (inverse_carry v1 + v2, 2026-10-09); pooling them let the challenger's
        # episodes confirm the champion, and `episode_groups` merged the two
        # arms' fills on one symbol into a single episode. The champion's book
        # is the evidence when it has one; a strategy that only runs in the
        # shadow wallet (neg_funding_carry) is judged on that.
        champion = [r for r in rs if not r.get("is_shadow")]
        arm = "champion" if champion else "shadow"
        rs, aborted = executable(champion or rs)
        eps = decompose(rs, components=())
        closed = [e for e in eps if e.closed and e.notional_usd]
        net = [float(e.net_bps) for e in closed]
        days = [e.opened_at.date().isoformat() for e in closed]
        n = len(net)
        mean = sum(net) / n if n else 0.0
        sd = math.sqrt(sum((x - mean) ** 2 for x in net) / (n - 1)) if n > 1 else 0.0
        n_days = len(set(days))
        t_day = (clustered_t(net, days) or 0.0) if n > 1 else 0.0
        # Too few day clusters: no test in either direction — neither `pays`
        # nor `harmful` may be read off a handful of funding regimes.
        thick = n_days >= CARRY_MIN_DAYS
        t = t_day if thick else 0.0
        p = two_sided_p(t) if thick else 1.0

        def _m(xs: list[float | None]) -> float | None:
            v = [x for x in xs if x is not None]
            return round(sum(v) / len(v), 2) if v else None

        rows.append({
            "strategy": sid,
            "market": ac,
            "kind": "carry",
            "arm": arm,
            "net_of_costs": True,
            "n": n,
            "n_raw": len(rs),
            "n_open": len(eps) - len(closed),
            "n_would_abort": len(aborted),
            "n_unscorable": 0,
            "n_days": n_days,
            "unit": SAMPLE_UNIT,
            "n_filled": n,
            "gross_bps": round(mean, 2),
            "control_bps": 0.0,
            "edge_bps": round(mean, 2),
            "t": round(t, 2),
            "t_day": round(t_day, 2),
            "side_edge_bps": 0.0,
            "t_side": 0.0,
            "p": round(p, 5),
            "significant": p < 0.05 and n >= MIN_TRADES,
            "control_side_bps": 0.0,
            "realised_net_bps": round(mean, 2),
            "sim_tp_rate": None,
            "sd_bps": round(sd, 2),
            "funding_bps": _m([e.bps(e.funding_usd) for e in closed]),
            "borrow_bps": _m([e.bps(e.borrow_usd) for e in closed]),
            "book_bps": _m([e.bps(e.book_usd) for e in closed]),
        })
        returns[(sid, ac)] = net
    return rows, returns


def apply_promotion_bar(
    rows: list[dict], returns: dict[tuple[str, str], list[float]], *, family: int, registry: Registry,
) -> bool:
    """Family-wide multiple-testing correction, deflated Sharpe, pre-registered
    n and `status`, in place, for directional and carry rows alike. Returns
    whether the registry changed (the caller saves it)."""
    testable = [r for r in rows if r["n"] >= MIN_TRADES]
    m = max(len(rows), family or 0, 1)
    # Hypotheses outside this run enter as p=1: the conservative stand-in for
    # tests we did not see, which puts every row we did see at the top ranks.
    pad = [1.0] * max(0, (family or 0) - len(testable))
    # Benjamini-Yekutieli, not Benjamini-Hochberg: every strategy here is scored
    # on the same bars, symbols and overlapping windows, so BH's independence
    # assumption is violated and its FDR guarantee does not hold. BHY is valid
    # under arbitrary dependence at a cost of H(m) ~ 3.2x at m=13. BH is kept
    # alongside so the two can be compared rather than argued about.
    keep_bh = benjamini_hochberg([r["p"] for r in testable] + pad)
    keep = benjamini_yekutieli([r["p"] for r in testable] + pad)
    keep_side = benjamini_yekutieli([two_sided_p(r["t_side"]) for r in testable] + pad)
    for r, k, ks, kbh in zip(testable, keep, keep_side, keep_bh):
        r["significant"] = bool(k)
        r["significant_bh"] = bool(kbh)
        r["significant_side"] = bool(ks)
        # The bar for keeping a strategy: beat at least one null convincingly.
        r["has_edge"] = bool((k and r["edge_bps"] > 0) or (ks and r["side_edge_bps"] > 0))
    for r in rows:
        r["family"] = m
        if r["n"] < MIN_TRADES:
            r["significant"] = r["significant_side"] = r["has_edge"] = False
            r["significant_bh"] = False

    # Deflate each Sharpe for the fact that we looked at every strategy, then
    # pre-register the sample size the survivors owe us.
    # Targets registered on rows-as-samples are void, not binding: the rule
    # was fine, the unit it was fed was wrong (momentum_xs's 936 came from a
    # burst of re-emissions). Mark them so `get` ignores them and a strategy
    # that still looks good on episodes registers afresh below.
    changed = registry.retire_stale() > 0
    for r in rows:
        arm = returns.get((r["strategy"], r["market"]), [])
        d = deflated_sharpe(arm, n_trials=m)
        r["dsr"] = round(d["dsr"], 4) if d else None
        r["sharpe"] = round(d["sharpe"], 4) if d else None
        if r.get("has_edge") and r.get("sd_bps"):
            edge = max(float(r["edge_bps"]), float(r.get("side_edge_bps") or 0.0))
            if registry.register(r["strategy"], r["market"], edge_bps=edge, sd_bps=float(r["sd_bps"])):
                changed = True
        reg = registry.get(r["strategy"], r["market"])
        r["required_n"] = int(reg["required_n"]) if reg else None
        r["status"] = promotion_status(
            r, registry=registry, n_trials=m, significant=bool(r.get("has_edge")),
        )
    return changed


async def run_edge_study(
    *, days: float = 14.0, strategy_id: str | None = None, draws: int = CONTROL_DRAWS, seed: int = 7,
    family: int | None = None,
) -> list[dict]:
    """Compare every strategy's real entries against random entries. One row per
    (strategy, market); rows are sorted by measured edge, best first.

    `family` is the number of hypotheses the multiple-testing corrections
    account for. A single-strategy run (the cached gate refreshes one strategy
    at a time) still belongs to the whole book's family: until 2026-10-09 it was
    corrected for m=1, so BHY was a bare p<0.05 and the deflated Sharpe was not
    deflated at all (dca reported DSR 0.99 on a +9 bps gross level). Default:
    every (strategy, market) that emitted a directional signal in the window.
    """
    rng = random.Random(seed)
    rows_in = await _load_candidates(days, strategy_id)
    carry_in = await _load_carry_fills(CARRY_DAYS, strategy_id)
    if not rows_in and not carry_in:
        logger.warning("edge study: no predictions in window")
        return []
    if family is None:
        family = await _family_size(days) if strategy_id else 0
    trades, n_raw = sample_episodes(rows_in, MAX_PER_STRATEGY)
    since = datetime.now(UTC) - timedelta(days=days + 1)
    by_class: dict[str, set[str]] = {}
    for t in trades:
        by_class.setdefault(t["asset_class"], set()).add(t["symbol"])
    bars: dict[tuple[str, str], list[Bar]] = {}
    for ac, syms in by_class.items():
        for sym, b in (await _load_bars(syms, ac, since)).items():
            bars[(ac, sym)] = b

    acc: dict[tuple[str, str], StrategyEdge] = {}
    skipped = 0
    unscorable: dict[tuple[str, str], int] = {}
    for t in trades:
        series = bars.get((t["asset_class"], t["symbol"]))
        if not series or len(series) < 30:
            skipped += 1
            continue
        horizon_bars = max(1, int((t["horizon_seconds"] or 600) // 60))
        tp = float(t["tp_pct"]) if t["tp_pct"] is not None else DEFAULT_TP_PCT
        sl = float(t["sl_pct"]) if t["sl_pct"] is not None else DEFAULT_SL_PCT
        key = (t["strategy_id"], t["asset_class"])
        idx = entry_index(series, t["generated_at"])
        if idx < 0 or idx >= len(series) - 1:
            skipped += 1
            unscorable[key] = unscorable.get(key, 0) + 1
            continue
        treat = simulate_bracket(series, idx, side=t["side"], tp_pct=tp, sl_pct=sl, horizon_bars=horizon_bars)
        if treat.reason == "no_data":
            skipped += 1
            unscorable[key] = unscorable.get(key, 0) + 1
            continue
        e = acc.setdefault(
            key, StrategyEdge(t["strategy_id"], t["asset_class"], n_raw=n_raw.get(key, 0))
        )
        e.n += 1
        e.treatment.append(treat.ret_bps)
        if t["filled"] and t["pnl_pct"] is not None:
            e.n_filled += 1
            e.realised_net_bps += float(t["pnl_pct"]) * _BPS
        if treat.reason == "hit_tp":
            e.tp_rate += 1
        upper = len(series) - horizon_bars - 1
        for _ in range(draws):
            if upper <= 1:
                break
            # Same entry rule as the treatment: open of the drawn bar, scored
            # from that bar on.
            c = simulate_bracket(
                series, rng.randint(0, upper), side=t["side"], tp_pct=tp, sl_pct=sl, horizon_bars=horizon_bars
            )
            if c.reason != "no_data":
                e.control.append(c.ret_bps)
            # Same moment, coin-flipped direction: does the strategy know which
            # way to go, independently of when?
            flip = "short" if rng.random() < 0.5 else "long"
            cs = simulate_bracket(
                series, idx, side=flip, tp_pct=tp, sl_pct=sl, horizon_bars=horizon_bars
            )
            if cs.reason != "no_data":
                e.control_side.append(cs.ret_bps)

    returns = {
        (e.strategy_id, e.asset_class): list(e.treatment) for e in acc.values()
    }
    rows = []
    for e in acc.values():
        e.n_unscorable = unscorable.get((e.strategy_id, e.asset_class), 0)
        if e.n_filled:
            e.realised_net_bps /= e.n_filled
        if e.n:
            e.tp_rate /= e.n
        rows.append({**e.as_row(), "kind": "directional"})

    # Carries: realised episodes, never simulated. A pair with directional
    # signals keeps its directional row (one status per strategy and market).
    c_rows, c_returns = carry_edge_rows(carry_in)
    directional = {(r["strategy"], r["market"]) for r in rows}
    for r in c_rows:
        k = (r["strategy"], r["market"])
        if k in directional:
            logger.warning(f"edge study: {k[0]}/{k[1]} emits directional and carry sides; "
                           "carry evidence not scored for it")
            continue
        rows.append(r)
        returns[k] = c_returns[k]

    # Multiple-testing correction across every strategy tested in this run,
    # deflated Sharpe and the pre-registered n. Advisory: a failure here must
    # never stop the study from returning its rows.
    try:
        registry = Registry.load()
        if apply_promotion_bar(rows, returns, family=family or 0, registry=registry):
            registry.save()
    except Exception as e:  # noqa: BLE001 — the promotion bar is advisory
        logger.warning(f"promotion bar unavailable ({e}); rows returned without it")
    rows.sort(key=lambda r: r["edge_bps"], reverse=True)
    if skipped:
        logger.info(f"edge study: {skipped} prediction(s) skipped (no bar coverage)")
    return rows


def _fmt_dsr(r: dict) -> str:
    v = r.get("dsr")
    return f"{v:.3f}" if v is not None else "-"


def _fmt_need(r: dict) -> str:
    v = r.get("required_n")
    return str(v) if v else "-"


def format_report(rows: list[dict], *, days: float, cost_bps: float) -> str:
    if not rows:
        return "edge study: no data"
    head = (
        f"Entry-timing edge vs random entry — last {days:g}d, "
        f"{sum(r['n'] for r in rows)} signals ({sum(r['n_filled'] for r in rows)} filled), "
        f"round-trip cost {cost_bps:g} bps\n"
        f"{'strategy':<24}{'mkt':<7}{'n':>6}{'gross':>8}{'vs time':>9}{'t':>6}"
        f"{'vs side':>9}{'t':>6}{'DSR':>7}{'need n':>8}  status\n"
    )
    lines = [
        f"{r['strategy']:<24}{r['market']:<7}{r['n']:>6}{r['gross_bps']:>8.1f}"
        f"{r['edge_bps']:>9.1f}{r['t']:>6.2f}{r['side_edge_bps']:>9.1f}{r['t_side']:>6.2f}"
        f"{_fmt_dsr(r):>7}{_fmt_need(r):>8}  {r.get('status', 'unproven')}"
        for r in rows
    ]
    total_t = [r for r in rows if r["n"] >= MIN_TRADES]
    tail = ""
    if total_t:
        best = max(total_t, key=lambda r: r["edge_bps"])
        n_sig = sum(1 for r in rows if r.get("has_edge"))
        tail = (
            f"\nbest: {best['strategy']}/{best['market']} edge {best['edge_bps']:+.1f} bps "
            f"(t={best['t']:.2f}, p={best['p']:.4f}, n={best['n']}). "
            f"{n_sig}/{len(total_t)} beat a null at FDR 5% (two families: random entry time "
            f"with the same side, and random side at the same time). A strategy that beats "
            f"neither has no measured reason to hold capital; an edge must also exceed "
            f"{cost_bps:g} bps to pay for itself.\n"
            f"Significance is Benjamini-Yekutieli (valid under the dependence these "
            f"overlapping tests actually have), DSR is the Sharpe deflated for having "
            f"searched {len(rows)} strategies, and 'need n' is the sample size registered "
            f"in advance for HALF this edge to stay detectable. Only `confirmed` — past "
            f"its own registered count and still significant — earns full size."
        )
    return head + "\n".join(lines) + tail


# ---------------------------------------------------------------- cached gate

_EDGE_TTL_S = float(os.environ.get("MATRIX_EDGE_CACHE_TTL_S", "21600"))  # 6 h
# Persisted next to the other measured models. Without this the cache dies with
# the process, `strategy_edge` answers None for the first minutes after every
# restart, and the slot scorer reads "no measured edge" as "no edge" — on
# 2026-09-20 that demoted momentum_xs from 7 slots to 3 within seconds of a
# reload, the one strategy whose edge is confirmed. Wall-clock timestamps, not
# monotonic, because the whole point is to outlive the process.
_CACHE_PATH = model_path("edge_cache.json")
_loaded_from_disk = False
_edge_cache: dict[tuple[str, str], tuple[float, dict | None]] = {}
# Keys whose refresh is already in flight, so a 5-second trading loop cannot
# queue thirteen concurrent studies while the first one is still running.
_refreshing: set[tuple[str, str]] = set()
# ...and only one of those queued refreshes may touch the database at a time.
# Moving the study off the tick stopped it blocking the engine, but thirteen
# background studies then loaded bars concurrently and put five 60-second
# `market_bars` reads on the database at once (2026-09-20). This is background
# work against a 6 h cache: there is no reason for any of it to be parallel.
_refresh_gate: asyncio.Semaphore | None = None


def _gate() -> asyncio.Semaphore:
    # Created lazily so the semaphore binds to the running loop, not to import.
    global _refresh_gate
    if _refresh_gate is None:
        _refresh_gate = asyncio.Semaphore(1)
    return _refresh_gate


def clear_cache() -> None:
    global _refresh_gate, _loaded_from_disk
    _edge_cache.clear()
    _refreshing.clear()
    _refresh_gate = None
    _loaded_from_disk = True  # tests own the cache; do not re-read the disk


def _load_disk_cache() -> None:
    global _loaded_from_disk
    if _loaded_from_disk:
        return
    _loaded_from_disk = True
    try:
        raw = json.loads(_CACHE_PATH.read_text())
    except FileNotFoundError:
        return
    except Exception as e:  # noqa: BLE001 — a corrupt cache is not worth a crash
        logger.warning(f"edge cache unreadable ({e}); starting cold")
        return
    now = _wall()
    for key, entry in raw.items():
        sid, _, market = key.partition("|")
        at = float(entry.get("at") or 0.0)
        if not sid or not market or now - at >= _EDGE_TTL_S:
            continue
        row = entry.get("row")
        # A row from before the episode fix is not stale, it is wrong: it
        # carries rows-as-samples t-stats into sizing and slot decisions for
        # up to six hours after the fix ships. Measure again instead.
        if row is not None and row.get("unit") != SAMPLE_UNIT:
            continue
        _edge_cache[(sid, market)] = (at, entry.get("row"))
    if _edge_cache:
        logger.info(f"edge cache: {len(_edge_cache)} row(s) restored from disk")


def _save_disk_cache() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            f"{sid}|{market}": {"at": at, "row": row}
            for (sid, market), (at, row) in _edge_cache.items()
        }
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(_CACHE_PATH)
    except Exception as e:  # noqa: BLE001 — persistence is a convenience
        logger.debug(f"edge cache not saved ({e})")


def _wall() -> float:
    import time as _t

    return _t.time()


def verdict(row: dict | None, *, cost_bps: float, min_t: float = 2.0) -> str:
    """`pays` | `harmful` | `unproven`, judged against BOTH nulls.

    `pays`    — beats a null (random entry time, or random side) by more than
                the round trip, at |t| >= min_t. Either kind of edge counts:
                knowing *when* and knowing *which way* are both tradeable.
    `harmful` — significantly WORSE than a null. A strategy whose direction
                loses to a coin flip is not mistuned, it is inverted, and no
                sizing or threshold change repairs that.
    """
    if not row or row["n"] < MIN_TRADES:
        return "unproven"
    # A carry row is realised net of fees, book and borrow already
    # (`carry_edge_rows`); charging the round trip again would count it twice.
    if row.get("net_of_costs"):
        cost_bps = 0.0
    t_time, t_side = row["t"], row.get("t_side", 0.0)
    e_time, e_side = row["edge_bps"], row.get("side_edge_bps", 0.0)
    beats_a_null = (t_time >= min_t and e_time >= cost_bps) or (
        t_side >= min_t and e_side >= cost_bps
    )
    # The wallet is paid the treatment arm's level, not its lead over a null.
    # A strategy can beat a losing control by more than the round trip and
    # still lose money on every trade, so `pays` also needs gross > cost.
    gross = row.get("gross_bps")
    if gross is not None and gross < cost_bps:
        beats_a_null = False
    # The promotion bar (matrix_shared.promotion): beating a null is necessary,
    # not sufficient. `pays` — the verdict that moves capital — additionally
    # requires the strategy to have reached the sample size it registered in
    # advance and to survive deflation for the number of strategies we searched.
    # A `provisional` strategy keeps emitting signals and keeps being measured;
    # it just does not get sized on a number that has not finished proving
    # itself. When `status` is absent (an older cached row, or a registry this
    # process cannot read) the bar is skipped rather than allowed to starve the
    # book — the promotion machinery is advisory in exactly the way the risk
    # gates are not.
    status = row.get("status")
    if beats_a_null and status is not None and status != "confirmed":
        return "unproven"
    if beats_a_null:
        return "pays"
    if (t_time <= -min_t and e_time < 0) or (t_side <= -min_t and e_side < 0):
        return "harmful"
    return "unproven"


async def strategy_edge(strategy_id: str, asset_class: str, *, days: float = 14.0) -> dict | None:
    """One strategy's edge row, cached for MATRIX_EDGE_CACHE_TTL_S. Advisory:
    returns None on any failure so a caller never blocks on this."""
    import time as _time

    _load_disk_cache()
    key = (strategy_id, asset_class)
    hit = _edge_cache.get(key)
    now = _wall()
    if hit and now - hit[0] < _EDGE_TTL_S:
        return hit[1]

    # Never run the study inline. It simulates thousands of brackets and the
    # caller is a 5-second trading loop: on 2026-09-20 raising the sample cap
    # to reach a pre-registered target stalled the paper engine for twenty
    # minutes, because thirteen strategy studies ran one after another inside
    # the tick. Refresh in the background and answer with what we already have;
    # a stale edge is a fine input to a decision about sizing, and `None` on a
    # cold start simply means the old sizing applies until the first study lands.
    if key not in _refreshing:
        _refreshing.add(key)
        asyncio.create_task(_refresh_edge(key, days))
    return hit[1] if hit else None


async def _refresh_edge(key: tuple[str, str], days: float) -> None:
    strategy_id, asset_class = key
    import time as _time

    try:
        async with _gate():
            await _run_refresh(key, days)
    finally:
        _refreshing.discard(key)


async def _run_refresh(key: tuple[str, str], days: float) -> None:
    strategy_id, asset_class = key
    import time as _time

    try:
        rows = await run_edge_study(days=days, strategy_id=strategy_id)
        row = next((r for r in rows if r["market"] == asset_class), None)
        _edge_cache[key] = (_wall(), row)
        _save_disk_cache()
    except Exception as e:  # noqa: BLE001 — advisory: never break the caller
        logger.debug(f"edge study for {strategy_id}/{asset_class} failed ({e})")
        # Back off by caching the miss, so a permanently failing study does not
        # respawn a task on every tick.
        _edge_cache[key] = (_wall(), _edge_cache.get(key, (0.0, None))[1])

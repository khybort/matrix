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
"""

from __future__ import annotations

import asyncio
import math
import os
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from loguru import logger

from matrix_shared.promotion import Registry, deflated_sharpe
from matrix_shared.promotion import status as promotion_status
from sqlalchemy import text

from matrix_shared.db import local_session_scope, shared_session_scope

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
DEFAULT_TP_PCT = 0.01
DEFAULT_SL_PCT = 0.005
_BPS = 10_000.0


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class SimResult:
    reason: str  # hit_tp | hit_sl | hit_horizon | no_data
    ret_bps: float
    bars_held: int


def simulate_bracket(
    bars: list[Bar], entry_idx: int, *, side: str, tp_pct: float, sl_pct: float, horizon_bars: int
) -> SimResult:
    """Replay one bracketed trade on 1m bars from `entry_idx` (entry = its close).

    Take-profit wins ties within a bar, matching the paper engine. Returns the
    gross return in bps, signed for the side.
    """
    if entry_idx < 0 or entry_idx >= len(bars) or horizon_bars <= 0:
        return SimResult("no_data", 0.0, 0)
    entry = bars[entry_idx].close
    if entry <= 0:
        return SimResult("no_data", 0.0, 0)
    long = side != "short"
    tp_px = entry * (1 + tp_pct) if long else entry * (1 - tp_pct)
    sl_px = entry * (1 - sl_pct) if long else entry * (1 + sl_pct)
    last = min(entry_idx + horizon_bars, len(bars) - 1)
    if last <= entry_idx:
        return SimResult("no_data", 0.0, 0)
    for i in range(entry_idx + 1, last + 1):
        b = bars[i]
        hit_tp = b.high >= tp_px if long else b.low <= tp_px
        hit_sl = b.low <= sl_px if long else b.high >= sl_px
        if hit_tp:
            return SimResult("hit_tp", tp_pct * _BPS, i - entry_idx)
        if hit_sl:
            return SimResult("hit_sl", -sl_pct * _BPS, i - entry_idx)
    exit_px = bars[last].close
    raw = (exit_px - entry) / entry
    return SimResult("hit_horizon", (raw if long else -raw) * _BPS, last - entry_idx)


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
        + ("  AND p.strategy_id = :sid " if strategy_id else "")
        + "ORDER BY p.generated_at"
    )
    params = {"secs": days * 86400}
    if strategy_id:
        params["sid"] = strategy_id
    async with shared_session_scope() as s:
        rows = (await s.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


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
            "SELECT symbol, ts, high, low, close FROM market_bars "
            "WHERE asset_class = :ac AND interval = '1m' AND ts >= :since "
            "AND symbol = ANY(:syms) ORDER BY symbol, ts"
        ), {"ac": asset_class, "since": since, "syms": list(symbols)})).all()
    out: dict[str, list[Bar]] = {}
    for sym, ts, high, low, close in rows:
        out.setdefault(sym, []).append(
            Bar(ts if ts.tzinfo else ts.replace(tzinfo=UTC), float(high), float(low), float(close))
        )
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


async def run_edge_study(
    *, days: float = 14.0, strategy_id: str | None = None, draws: int = CONTROL_DRAWS, seed: int = 7
) -> list[dict]:
    """Compare every strategy's real entries against random entries. One row per
    (strategy, market); rows are sorted by measured edge, best first."""
    rng = random.Random(seed)
    rows_in = await _load_candidates(days, strategy_id)
    if not rows_in:
        logger.warning("edge study: no predictions in window")
        return []
    grouped: dict[tuple[str, str], list[dict]] = {}
    for r in rows_in:
        grouped.setdefault((r["strategy_id"], r["asset_class"]), []).append(r)
    trades = [r for g in grouped.values() for r in subsample(g, MAX_PER_STRATEGY)]
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
    for t in trades:
        series = bars.get((t["asset_class"], t["symbol"]))
        if not series or len(series) < 30:
            skipped += 1
            continue
        horizon_bars = max(1, int((t["horizon_seconds"] or 600) // 60))
        tp = float(t["tp_pct"]) if t["tp_pct"] is not None else DEFAULT_TP_PCT
        sl = float(t["sl_pct"]) if t["sl_pct"] is not None else DEFAULT_SL_PCT
        idx = _index_at(series, t["generated_at"])
        if idx < 0 or idx >= len(series) - 1:
            skipped += 1
            continue
        treat = simulate_bracket(series, idx, side=t["side"], tp_pct=tp, sl_pct=sl, horizon_bars=horizon_bars)
        if treat.reason == "no_data":
            skipped += 1
            continue
        key = (t["strategy_id"], t["asset_class"])
        e = acc.setdefault(key, StrategyEdge(t["strategy_id"], t["asset_class"]))
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

    _treatment_returns = {
        (e.strategy_id, e.asset_class): list(e.treatment) for e in acc.values()
    }
    rows = []
    for e in acc.values():
        if e.n_filled:
            e.realised_net_bps /= e.n_filled
        if e.n:
            e.tp_rate /= e.n
        rows.append(e.as_row())
    # Multiple-testing correction across every strategy tested in this run.
    testable = [r for r in rows if r["n"] >= MIN_TRADES]
    # Benjamini-Yekutieli, not Benjamini-Hochberg: every strategy here is scored
    # on the same bars, symbols and overlapping windows, so BH's independence
    # assumption is violated and its FDR guarantee does not hold. BHY is valid
    # under arbitrary dependence at a cost of H(m) ~ 3.2x at m=13. BH is kept
    # alongside so the two can be compared rather than argued about.
    keep_bh = benjamini_hochberg([r["p"] for r in testable])
    keep = benjamini_yekutieli([r["p"] for r in testable])
    keep_side = benjamini_yekutieli([two_sided_p(r["t_side"]) for r in testable])
    for r, k, ks, kbh in zip(testable, keep, keep_side, keep_bh):
        r["significant"] = bool(k)
        r["significant_bh"] = bool(kbh)
        r["significant_side"] = bool(ks)
        # The bar for keeping a strategy: beat at least one null convincingly.
        r["has_edge"] = bool((k and r["edge_bps"] > 0) or (ks and r["side_edge_bps"] > 0))
    for r in rows:
        if r["n"] < MIN_TRADES:
            r["significant"] = r["significant_side"] = r["has_edge"] = False
            r["significant_bh"] = False

    # Deflate each Sharpe for the fact that we looked at every strategy, then
    # pre-register the sample size the survivors owe us. Advisory: a failure
    # here must never stop the study from returning its rows.
    try:
        registry = Registry.load()
        changed = False
        for r in rows:
            arm = _treatment_returns.get((r["strategy"], r["market"]), [])
            d = deflated_sharpe(arm, n_trials=max(1, len(rows)))
            r["dsr"] = round(d["dsr"], 4) if d else None
            r["sharpe"] = round(d["sharpe"], 4) if d else None
            if r.get("has_edge") and r.get("sd_bps"):
                edge = max(float(r["edge_bps"]), float(r.get("side_edge_bps") or 0.0))
                if registry.register(
                    r["strategy"], r["market"], edge_bps=edge, sd_bps=float(r["sd_bps"])
                ):
                    changed = True
            reg = registry.get(r["strategy"], r["market"])
            r["required_n"] = int(reg["required_n"]) if reg else None
            r["status"] = promotion_status(
                r, registry=registry, n_trials=max(1, len(rows)),
                significant=bool(r.get("has_edge")),
            )
        if changed:
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
    global _refresh_gate
    _edge_cache.clear()
    _refreshing.clear()
    _refresh_gate = None


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
    t_time, t_side = row["t"], row.get("t_side", 0.0)
    e_time, e_side = row["edge_bps"], row.get("side_edge_bps", 0.0)
    beats_a_null = (t_time >= min_t and e_time >= cost_bps) or (
        t_side >= min_t and e_side >= cost_bps
    )
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

    key = (strategy_id, asset_class)
    hit = _edge_cache.get(key)
    now = _time.monotonic()
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
        _edge_cache[key] = (_time.monotonic(), row)
    except Exception as e:  # noqa: BLE001 — advisory: never break the caller
        logger.debug(f"edge study for {strategy_id}/{asset_class} failed ({e})")
        # Back off by caching the miss, so a permanently failing study does not
        # respawn a task on every tick.
        _edge_cache[key] = (_time.monotonic(), _edge_cache.get(key, (0.0, None))[1])

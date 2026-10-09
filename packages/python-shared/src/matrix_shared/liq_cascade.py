"""Liquidation-cascade fade (forward test r2f): one definition.

Round 2's H8b-5m ("fade a 5-minute cascade") passed train (+26…+49 bps net,
t_day 2.5–2.9) and failed holdout (t ≤ 1.05). It had to use a proxy, a burst
of same-side aggressor volume, because no historical liquidation feed exists.
`ingestion.liquidation_recorder` now records Bybit's public `allLiquidation`
stream for every USDT perp (`bybit_liquidations`, local tier, migration 0044)
together with the minutes the feed was demonstrably up
(`bybit_liquidation_minutes`). The rule below replaces the proxy leg with real
liquidations and keeps round 2's displacement leg unchanged. Three places run
it and must agree, so it lives here once:

  * the shadow module `liq_cascade_fade` (strategy service), live;
  * the r2f evaluation builder (services/backtest/research/signal_2026_10_r2f);
  * the tests.

Rule (r2f PREREG), at every 1-minute close tau of a Bybit USDT perp:
  window   = the five minutes [tau - 5m, tau); every one of them must be a
             covered minute (feed up), else no signal;
  L, S     = USD notional (size x bankruptcy price) of LONG positions
             liquidated (Bybit side "Buy") and of SHORT positions liquidated
             ("Sell") with timestamp in the window;
  Q        = 99th percentile (nearest rank) of the symbol's one-sided window
             notional, both sides pooled, over every covered window end in the
             7 days before the start of tau's UTC hour (>= 6 days covered, else
             no signal); zero windows count;
  r5       = ln close(tau) - ln close(tau - 5m), Bybit 1m klines;
  sigma5   = sample SD of r5 at the 1440 minute closes ending tau - 5m
             (>= 720 values) — round 2's H8b-5m displacement, unchanged;
  liquidity: turnover of the 1440 1m bars ending at tau >= $2M;
  fire LONG  (fade the dump) when L >= max(Q, $25k) and r5 <= -max(2 %, 5 sigma5),
  fire SHORT (fade the squeeze) when S >= max(Q, $25k) and r5 >= +max(2 %, 5 sigma5);
  both at once -> no signal.
  Entry at the close of the bar ending tau + 1m (one full bar after the
  window, as round 2); exit at the close of the bar ending entry + hold;
  one episode per (cell, symbol) at a time.

Pure standard library, Python 3.9-compatible (the host research python imports it).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

UTC = timezone.utc  # noqa: UP017 — datetime.UTC is 3.11+, the host python is 3.9

MIN_MS = 60_000
H_MS = 60 * MIN_MS
DAY_MS = 24 * H_MS
WINDOW_MIN = 5
PCT = 0.99
LOOKBACK_MS = 7 * DAY_MS
MIN_COVERED_ENDS = 6 * 1440  # covered window ends in the lookback
FLOOR_USD = 25_000.0
DISP_MIN = 0.02
DISP_SIGMA = 5.0
SIGMA_N = 1440
SIGMA_MIN_N = 720
MIN_TURNOVER_USD = 2_000_000.0
ENTRY_LAG_MIN = 1
HOLDS_MIN = (60, 240)
TAKER_BPS = 5.5
# round 2's turnover -> spread map (log s = a + b log turnover, 7 137 tick
# symbol-days); x 2.2 = round 2's H8b-5m measured event spread / map (6.88 / 3.14 bps)
SPREAD_A = 4.533917440199184
SPREAD_B = -0.21273389045888394
EVENT_SPREAD_MULT = 2.2

# Bybit allLiquidation side is the POSITION side: "Buy" = a long was liquidated.
SIDE_OF = {"Buy": "long", "Sell": "short"}


def to_ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def minute_floor(ms: int) -> int:
    return ms - ms % MIN_MS


def bucket(rows: Iterable[tuple[int, str, float]]) -> dict[int, list[float]]:
    """(ts ms, 'long'|'short', notional usd) -> minute start ms -> [long usd, short usd]."""
    out: dict[int, list[float]] = {}
    for ts, side, usd in rows:
        b = out.setdefault(minute_floor(ts), [0.0, 0.0])
        b[0 if side == "long" else 1] += usd
    return out


def window_minutes(tau: int) -> range:
    return range(tau - WINDOW_MIN * MIN_MS, tau, MIN_MS)


def window_covered(covered: Mapping[int, object] | set, tau: int) -> bool:
    return all(m in covered for m in window_minutes(tau))


def window_sums(liq: Mapping[int, list[float]], tau: int) -> tuple[float, float]:
    lo = sh = 0.0
    for m in window_minutes(tau):
        b = liq.get(m)
        if b:
            lo += b[0]
            sh += b[1]
    return lo, sh


def threshold(liq: Mapping[int, list[float]], covered: Mapping[int, object] | set, tau: int) -> float | None:
    """Q at tau (same for every tau in one UTC hour); None = < 6 days covered."""
    hour = tau - tau % H_MS
    lo_end, hi_end = hour - LOOKBACK_MS + MIN_MS, hour  # window ends in (hour - 7d, hour]
    n_cov = sum(1 for e in range(lo_end, hi_end + MIN_MS, MIN_MS) if window_covered(covered, e))
    if n_cov < MIN_COVERED_ENDS:
        return None
    ends = set()
    for m in liq:
        if lo_end - WINDOW_MIN * MIN_MS <= m <= hi_end:
            for k in range(1, WINDOW_MIN + 1):
                e = m + k * MIN_MS
                if lo_end <= e <= hi_end:
                    ends.add(e)
    vals = []
    for e in ends:
        if window_covered(covered, e):
            vals.extend(v for v in window_sums(liq, e) if v > 0)
    total = 2 * n_cov
    rank = math.ceil(PCT * total)  # nearest rank, 1-based
    zeros = total - len(vals)
    if rank <= zeros:
        return 0.0
    vals.sort()
    return vals[rank - zeros - 1]


def displacement(closes: Mapping[int, float], tau: int) -> tuple[float | None, float | None]:
    """(r5 at tau, sigma5 over the 1440 r5 ending tau - 5m); None when missing.
    `closes`: bar END ms -> close."""
    def r5(e: int) -> float | None:
        a, b = closes.get(e), closes.get(e - WINDOW_MIN * MIN_MS)
        return math.log(a / b) if a and b else None

    now = r5(tau)
    hist = [x for x in (r5(tau - (WINDOW_MIN + i) * MIN_MS) for i in range(SIGMA_N)) if x is not None]
    sig = statistics.stdev(hist) if len(hist) >= SIGMA_MIN_N else None
    return now, sig


def turnover_24h(turnover: Mapping[int, float], tau: int) -> float:
    """Sum of 1m-bar turnover (bar END ms -> usd) over the 1440 bars ending at tau."""
    return sum(turnover.get(tau - i * MIN_MS, 0.0) for i in range(1440))


def spread_bps(turnover_usd: float) -> float:
    """Round-trip spread charged on an event: round 2's map x EVENT_SPREAD_MULT."""
    t = max(turnover_usd, 1.0)
    return EVENT_SPREAD_MULT * math.exp(SPREAD_A + SPREAD_B * math.log(t))


def cost_bps(turnover_usd: float) -> float:
    return 2 * TAKER_BPS + spread_bps(turnover_usd)


def evaluate(
    tau: int,
    liq: Mapping[int, list[float]],
    covered: Mapping[int, object] | set,
    closes: Mapping[int, float],
    turnover: Mapping[int, float],
    q: float | None = None,
) -> dict:
    """The decision at minute close tau for one symbol. `q` may be passed when
    the caller caches Q per hour. Returns a dict with `side` ('long' | 'short'
    | None) and every input, plus `reason` when it does not fire."""
    out: dict = {"tau": tau, "side": None, "reason": None, "L": None, "S": None, "Q": q,
                 "r5": None, "sigma5": None, "turnover": None}
    if not window_covered(covered, tau):
        out["reason"] = "window not covered"
        return out
    lo, sh = window_sums(liq, tau)
    out["L"], out["S"] = lo, sh
    if max(lo, sh) < FLOOR_USD:
        out["reason"] = "below floor"
        return out
    if q is None:
        q = threshold(liq, covered, tau)
        out["Q"] = q
    if q is None:
        out["reason"] = "< 6 days covered"
        return out
    bar = max(q, FLOOR_USD)
    long_liq, short_liq = lo >= bar, sh >= bar
    if not (long_liq or short_liq):
        out["reason"] = "below p99"
        return out
    r5, sig = displacement(closes, tau)
    out["r5"], out["sigma5"] = r5, sig
    if r5 is None or sig is None:
        out["reason"] = "no price history"
        return out
    move = max(DISP_MIN, DISP_SIGMA * sig)
    fade_long = long_liq and r5 <= -move
    fade_short = short_liq and r5 >= move
    if fade_long == fade_short:
        out["reason"] = "both sides" if fade_long else "no displacement"
        return out
    to = turnover_24h(turnover, tau)
    out["turnover"] = to
    if to < MIN_TURNOVER_USD:
        out["reason"] = "illiquid"
        return out
    out["side"] = "long" if fade_long else "short"
    return out


def candidates(liq: Mapping[int, list[float]], covered, start: int, end: int) -> list[int]:
    """Minute closes tau in [start, end) whose window has a side >= FLOOR_USD
    (the only ones that can fire), ascending."""
    taus = set()
    for m in liq:
        for k in range(1, WINDOW_MIN + 1):
            e = m + k * MIN_MS
            if start <= e < end:
                taus.add(e)
    return sorted(t for t in taus if max(window_sums(liq, t)) >= FLOOR_USD and window_covered(covered, t))

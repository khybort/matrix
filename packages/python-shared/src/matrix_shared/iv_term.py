"""Front-end IV inversion (round 5 cell D.3, forward test r5f): one definition.

Round 5 (docs/wiki/signal-research-2026-10.md, "Round 5: options-implied")
found its near miss in D.3: when front-month ATM implied vol trades far above
the back month (acute stress), long the BTC / ETH perp for 3 days. Positive in
both splits (+136 / +115 bps per episode, medians +166 / +201) but train t
0.77, so it failed; the forward test r5f
(services/backtest/research/signal_2026_10_r5f/PREREG.md) settles it. Three
places run the rule and must agree to the bit, so it lives here once:

  * `ingestion.iv_term_recorder` turns the day's Deribit option trades into
    `term_features` and stores them (`deribit_iv_daily`, local tier);
  * the shadow module `iv_inversion` reads that table and calls `decisions`;
  * the r5f evaluation builder replays the same table through `decisions`.

Rule (round 5 PREREG, frozen in 1b2b6e1; code: signal_2026_10_r5/research.py):
  decision time D = 00:00 UTC; inputs are the option trades in [D-4h, D);
  Black-76 delta with F = index_price, r = 0, T = time to the 08:00 UTC expiry;
  TERM = median iv of |delta| in [0.40, 0.60] trades with 1.5 <= DTE <= 10
         minus the same with 45 <= DTE <= 120 (missing if either has < 5);
  pct  = share of the asset's non-missing TERM values in the 365 calendar days
         before D that are < TERM_D (>= 120 such values, else no signal);
  fire when pct >= 0.90. A signal window is a maximal run of consecutive
  calendar days that fire; one episode per window, entered on its first day at
  the close of the 1h bar ending D + 1h, held 72 h; a window that starts while
  an episode is open is skipped entirely.

Pure standard library, Python 3.9-compatible (the host research python imports it).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone

UTC = timezone.utc  # noqa: UP017 — datetime.UTC is 3.11+, the host python is 3.9

H_MS = 3_600_000
DAY_MS = 24 * H_MS
WINDOW_H = 4  # trades in [D - 4h, D)
THRESHOLD = 0.90
LOOKBACK_DAYS = 365
MIN_HISTORY = 120
HOLD_H = 72
ENTRY_LAG_H = 1
MIN_BUCKET = 5
FRONT_DTE = (1.5, 10.0)
BACK_DTE = (45.0, 120.0)
ATM_DELTA = (0.40, 0.60)
CURRENCIES = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

_MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


def parse_instrument(name: str) -> tuple[int, float, str]:
    """'BTC-27DEC24-60000-C' -> (expiry ms at 08:00 UTC, strike, 'C' | 'P')."""
    _, exp, strike, typ = name.split("-")
    d, mon, yy = int(exp[:-5]), _MON[exp[-5:-2]], 2000 + int(exp[-2:])
    return int(datetime(yy, mon, d, 8, tzinfo=UTC).timestamp() * 1000), float(strike), typ


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def term_features(trades: Iterable[Mapping], day_ms: int) -> dict:
    """Features of decision day `day_ms` (00:00 UTC) from option trades, each a
    mapping with timestamp, instrument_name, iv, index_price, amount. Trades
    outside [D - 4h, D) are ignored. TERM is None when a bucket is thin."""
    lo = day_ms - WINDOW_H * H_MS
    n = 0
    put = call = 0.0
    front: list[float] = []
    back: list[float] = []
    for t in trades:
        ts = int(t["timestamp"])
        if not lo <= ts < day_ms:
            continue
        n += 1
        exp, strike, typ = parse_instrument(t["instrument_name"])
        idx = float(t["index_price"])
        notional = float(t["amount"]) * idx
        if typ == "P":
            put += notional
        else:
            call += notional
        iv = t.get("iv")
        if iv is None or not iv > 0:
            continue
        years = (exp - ts) / (365 * DAY_MS)
        if not years > 0:
            continue
        sig = float(iv) / 100
        d1 = (math.log(idx / strike) + 0.5 * sig * sig * years) / (sig * math.sqrt(years))
        delta = _ncdf(d1) if typ == "C" else _ncdf(d1) - 1
        if not ATM_DELTA[0] <= abs(delta) <= ATM_DELTA[1]:
            continue
        dte = years * 365
        if FRONT_DTE[0] <= dte <= FRONT_DTE[1]:
            front.append(float(iv))
        elif BACK_DTE[0] <= dte <= BACK_DTE[1]:
            back.append(float(iv))
    f = statistics.median(front) if len(front) >= MIN_BUCKET else None
    b = statistics.median(back) if len(back) >= MIN_BUCKET else None
    return {
        "n_trades": n, "put_notional": put, "call_notional": call,
        "front_iv": statistics.median(front) if front else None,
        "back_iv": statistics.median(back) if back else None,
        "n_front": len(front), "n_back": len(back),
        "term": f - b if f is not None and b is not None else None,
    }


def day_floor(t: datetime) -> datetime:
    t = t.astimezone(UTC)
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def to_ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def percentile(history: Mapping[int, float | None], day_ms: int) -> float | None:
    """pct(TERM)_D: share of non-missing values in [D - 365 d, D) below TERM_D."""
    x = history.get(day_ms)
    if x is None:
        return None
    lo = day_ms - LOOKBACK_DAYS * DAY_MS
    hist = [v for d, v in history.items() if lo <= d < day_ms and v is not None]
    if len(hist) < MIN_HISTORY:
        return None
    return sum(1 for v in hist if v < x) / len(hist)


def decisions(
    history: Mapping[int, float | None],
    start_ms: int | None = None,
    end_ms: int | None = None,
    *,
    threshold: float = THRESHOLD,
    hold_h: float = HOLD_H,
    entry_lag_h: float = ENTRY_LAG_H,
) -> list[dict]:
    """One row per calendar decision day in [start, end] (both ends inclusive,
    default: the history's span): day, term, pct, on, enter, entry_ms, exit_ms.
    `history` maps day ms (00:00 UTC) -> TERM (None = missing); a day with no
    key is missing too. The window/open-episode state is replayed from the
    history's first day so `start` does not change any verdict."""
    if not history:
        return []
    first = min(history)
    last = max(history) if end_ms is None else end_ms
    start = first if start_ms is None else start_ms
    out: list[dict] = []
    open_until = -1
    prev_on = False
    day = first
    while day <= last:
        pct = percentile(history, day)
        on = pct is not None and pct >= threshold
        enter = False
        entry = exit_ = None
        if on and not prev_on and day >= open_until:  # a window starts and nothing is open
            entry = day + int(entry_lag_h * H_MS)
            exit_ = entry + int(hold_h * H_MS)
            open_until = exit_
            enter = True
        prev_on = on
        if day >= start:
            out.append({"day": day, "term": history.get(day), "pct": pct, "on": on, "enter": enter,
                        "entry_ms": entry, "exit_ms": exit_})
        day += DAY_MS
    return out

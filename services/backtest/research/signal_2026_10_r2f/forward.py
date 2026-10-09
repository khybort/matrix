"""Forward test r2f: round 2's H8b-5m cascade fade, re-specified on REAL Bybit liquidations, n = 1000.

usage: python3 forward.py <data dir> register   # 1st: writes PREREG.md (commit it ALONE); 2nd: ledger register rows
       python3 forward.py <data dir> status     # closed forward episodes of 1000 per cell (counts, never returns)
       python3 forward.py <data dir> decide     # once per cell, when status says due: the decision + final verdict

<data dir>/liq.csv and <data dir>/minutes.csv are the recorder's tables, exported on the node that runs ingestion:
  docker exec matrix-postgres psql -U matrix -d matrix -c \\
    "\\copy (SELECT symbol, (extract(epoch FROM ts) * 1000)::bigint AS ts_ms, side, notional_usd
             FROM bybit_liquidations ORDER BY ts) TO STDOUT CSV HEADER" > <dir>/liq.csv
  docker exec matrix-postgres psql -U matrix -d matrix -c \\
    "\\copy (SELECT (extract(epoch FROM minute) * 1000)::bigint AS minute_ms
             FROM bybit_liquidation_minutes ORDER BY 1) TO STDOUT CSV HEADER" > <dir>/minutes.csv
Bybit 1m klines and funding are fetched (cached under <dir>/cache) at run time.

The rule is matrix_shared.liq_cascade (the same code the shadow module runs).
Only the P&L of an episode lives here.
"""

import csv
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness_2026_10"))
import _path  # noqa: E402,F401

from matrix_shared import liq_cascade as L  # noqa: E402
from matrix_shared.research import Cell, Episode, NotCommitted, Spec, register  # noqa: E402
from matrix_shared.research.fetch import PoliteFetcher, bybit_funding, bybit_klines  # noqa: E402
from matrix_shared.research.funding import funding_bps  # noqa: E402

UTC = timezone.utc
PREREG = "services/backtest/research/signal_2026_10_r2f/PREREG.md"
N = 1000
RULE = (
    "at each 1m close tau of a Bybit USDT perp: L / S = USD notional (size x bankruptcy price) of longs / shorts "
    "liquidated in [tau-5m, tau), all 5 minutes covered by the recorder; Q = 99th percentile (nearest rank) of the "
    "symbol's one-sided 5m notional, both sides pooled, over every covered window end in the 7 days before tau's "
    "UTC hour (>= 6 days covered, zero windows count); r5 = ln close(tau) - ln close(tau-5m); sigma5 = sample SD of "
    "r5 at the 1440 minute closes ending tau-5m (>= 720). LONG when L >= max(Q, $25k) and r5 <= -max(2%, 5 sigma5); "
    "SHORT when S >= max(Q, $25k) and r5 >= +max(2%, 5 sigma5); both -> none; trailing-24h 1m turnover >= $2M"
)
COMMON = {
    "rule": RULE,
    "code": "matrix_shared.liq_cascade (evaluate, threshold, displacement)",
    "universe": "every Bybit USDT linear perpetual the recorder streams (all trading ones, re-read hourly)",
    "entry": "close of the 1m bar ending tau + 1m (one full bar after the window, as round 2)",
    "episodes": "one per (cell, symbol) at a time: a signal before the previous episode's exit is dropped",
    "cost": "2 x 5.5 bps taker + 2.2 x round 2's turnover spread map at the trailing-24h turnover + funding",
    "n": N,
}

SPEC = Spec(
    round="r2f",
    family="H8b",
    title="forward test of round 2 H8b-5m on real liquidations: fade a 5-minute Bybit liquidation cascade",
    question=(
        "Round 2's H8b-5m fade passed train (+25.8 / +32.0 / +49.0 bps net at 30 / 60 / 240 min, t_day 2.5-2.9) "
        "and failed holdout (+9.4 / +18.1 / -2.0, t_day <= 1.05) on a PROXY: same-side aggressor bursts, because no "
        "historical liquidation feed exists. With the proxy leg replaced by real Bybit liquidations (5-minute "
        "one-sided notional >= the symbol's trailing p99) and round 2's displacement leg kept, does fading the "
        "cascade earn net > 0 at day-clustered t >= 2 over its first 1000 forward episodes, and survive BHY at "
        "the programme's cumulative m?"
    ),
    cells=(
        Cell("L5.60", {**COMMON, "hold_min": 60}),
        Cell("L5.240", {**COMMON, "hold_min": 240}),
    ),
    train=("2026-05-01", "2026-10-09"),  # round 2's two splits on the proxy: cited, not re-tested
    holdout=("2026-10-17", "2028-10-17"),  # forward window by signal time; Q needs 6 covered days first
    forward_n=N,
    cluster="day",
    min_t=2.0,
    q_max=0.05,
    min_entry_lag_s=60,
    data=(
        "Liquidations from `bybit_liquidations` and feed coverage from `bybit_liquidation_minutes` (local tier, "
        "migration 0044), written since 2026-10-09 18:10 UTC by ingestion.liquidation_recorder from Bybit's public "
        "WebSocket topic allLiquidation.<symbol> for every trading USDT perp (791 at registration). Side = the "
        "position liquidated (Bybit 'Buy' = long). A minute is covered only if every socket was subscribed before "
        "it began and live when it ended, written after its events were flushed; a window touching an uncovered "
        "minute cannot fire. Prices: Bybit linear 1m klines (close, turnover) and funding history, fetched at "
        "evaluation."
    ),
    costs=(
        "net = side x (close(exit) / close(entry) - 1) + funding - cost, bps of entry notional. funding: every "
        "Bybit settlement in (entry, exit], long pays a positive rate, notional marked to market "
        "(research.funding.funding_bps). cost = 11 bps taker + 2.2 x exp(4.534 - 0.2127 ln turnover24h): round 2's "
        "kline spread map times its H8b-5m event-spread ratio (measured tick spread proxy 6.88 bps vs map 3.14 bps "
        "over its 866 episodes), so ~17 bps at $14M/day, as round 2's H8b-5m cost (17-19 bps)."
    ),
    survivorship=(
        "Forward data on every perp trading at each signal; delisted coins stay in the sample up to delisting. "
        "An episode whose exit bar does not exist (delisted mid-hold) is excluded and counted as unpriced."
    ),
    notes=(
        "n = 1000 per cell is fixed here, before any forward data exists, from round 2's per-episode SD and its "
        "day-clustering design effect: 60 min SD 226 bps, deff 1.87 (SD_eff 310); 240 min SD 354, deff 1.21 "
        "(SD_eff 388). At round 2's pooled train+holdout mean (+25 bps at 60 min, +24 at 240) n = 1000 gives E[t] "
        "2.55 / 2.04, P(t >= 2) 0.71 / 0.51; at +40 bps 4.08 / 3.26, P 0.98 / 0.90. The ledger q gate at m ~ 174 "
        "(BY, rank 2 behind r1.H1) needs one-sided p <= ~1e-4, t ~ 3.7: P 0.13 / 0.05 at +25 bps, 0.65 / 0.33 at "
        "+40. A pass is strong evidence; a fail at +25 does not rule out a smaller edge, and no re-run with a larger "
        "n under these ids is allowed. Signal rate is unknown until the feed has run; round 2's proxy fired ~6 a day "
        "on 40 symbols. If 1000 episodes of a cell have not closed by 2028-10-17 that cell is never decided (family "
        "p stays 1). The shadow module `liq_cascade_fade` trades the 60 min cell on the shadow wallet for the symbols "
        "the node streams; its fills are monitoring only (the shadow tracker reports `collecting` until 1000). The "
        "decision is this harness evaluation, once per cell, at n; nothing is evaluated earlier and no early stop "
        "exists."
    ),
)


def load(data: Path) -> tuple[dict, set]:
    rows: dict[str, list] = {}
    with (data / "liq.csv").open() as fh:
        for r in csv.DictReader(fh):
            rows.setdefault(r["symbol"], []).append((int(r["ts_ms"]), r["side"], float(r["notional_usd"])))
    with (data / "minutes.csv").open() as fh:
        cov = {int(r["minute_ms"]) for r in csv.DictReader(fh)}
    return {s: L.bucket(v) for s, v in rows.items()}, cov


def make_builder(data: Path):
    liq, cov = load(data)
    f = PoliteFetcher(data / "cache")
    now_ms = int(time.time() * 1000)
    bars: dict = {}

    def day_bars(sym: str, day: int) -> tuple[dict, dict]:
        if (sym, day) not in bars:
            kl = bybit_klines(f, "linear", sym, "1", day, min(day + L.DAY_MS - 1, now_ms - 2 * L.MIN_MS))
            bars[(sym, day)] = ({k[0] + L.MIN_MS: k[4] for k in kl}, {k[0] + L.MIN_MS: k[6] for k in kl})
        return bars[(sym, day)]

    def window(sym: str, lo: int, hi: int) -> tuple[dict, dict]:
        closes: dict = {}
        to: dict = {}
        d = lo - lo % L.DAY_MS
        while d <= hi:
            c, t = day_bars(sym, d)
            closes.update(c)
            to.update(t)
            d += L.DAY_MS
        return closes, to

    def builder(cell, start, end):
        hold = cell.params["hold_min"] * L.MIN_MS
        lo, hi = L.to_ms(start), min(L.to_ms(end), now_ms)
        for sym, lq in liq.items():
            qcache: dict = {}
            fund = None
            for tau in L.candidates(lq, cov, lo, hi):
                hour = tau - tau % L.H_MS
                if hour not in qcache:
                    qcache[hour] = L.threshold(lq, cov, tau)
                q = qcache[hour]
                if q is None or max(L.window_sums(lq, tau)) < max(q, L.FLOOR_USD):
                    continue
                t_entry = tau + L.ENTRY_LAG_MIN * L.MIN_MS
                t_exit = t_entry + hold
                closes, to = window(sym, tau - L.DAY_MS - 15 * L.MIN_MS, t_exit)
                d = L.evaluate(tau, lq, cov, closes, to, q=q)
                if d["side"] is None:
                    continue
                side = 1 if d["side"] == "long" else -1
                info = datetime.fromtimestamp(tau / 1000, UTC)
                entry = datetime.fromtimestamp(t_entry / 1000, UTC)
                exit_ = datetime.fromtimestamp(t_exit / 1000, UTC)
                p0, p1 = closes.get(t_entry), closes.get(t_exit)
                if p0 is None or p1 is None or t_exit > now_ms:
                    yield Episode(sym, info, entry, exit_, math.nan)  # open (or unpriced) — the harness decides
                    continue
                if fund is None:
                    fund = [(datetime.fromtimestamp(ts / 1000, UTC), rate)
                            for ts, rate in bybit_funding(f, sym, lo, hi + L.DAY_MS)]
                price = side * (p1 / p0 - 1) * 1e4
                hourly = {k: v for k, v in closes.items() if k % L.H_MS == 0}
                fbps, _ = funding_bps(fund, entry, exit_, side, p0, lambda t: hourly.get(int(t.timestamp() * 1000)))
                cost = L.cost_bps(d["turnover"])
                yield Episode(sym, info, entry, exit_, price + fbps - cost,
                              {"price": price, "funding": fbps, "cost": cost, "L": d["L"], "S": d["S"],
                               "Q": d["Q"], "r5": d["r5"], "sigma5": d["sigma5"], "turnover": d["turnover"]})

    return builder


if __name__ == "__main__":
    data, mode = Path(sys.argv[1]), sys.argv[2]
    try:
        study = register(SPEC, PREREG)
    except NotCommitted as e:
        print(e)
        sys.exit(0)
    if mode == "register":
        print(f"registered: {[c.id for c in SPEC.cells]} replicate={study.replicate}")
    elif mode == "status":
        b = make_builder(data)
        for c in SPEC.cells:
            print(c.id, study.forward_status(c.id, b))
    elif mode == "decide":
        b = make_builder(data)
        for c in SPEC.cells:
            st = study.forward_status(c.id, b)
            if not st["due"]:
                print(f"{c.id} not due: {st}")
                continue
            print(study.open_forward(c.id, b).row())
        try:
            print(study.finalise())
        except Exception as e:  # noqa: BLE001 — finalise waits until every cell is decided
            print(f"finalise: {e}")

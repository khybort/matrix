"""Forward test r5f: round 5 cell D.3 (front-end IV inversion -> long BTC/ETH perp 3 days), n = 60.

usage: python3 forward.py <data dir> register   # 1st: writes PREREG.md (commit it ALONE); 2nd: ledger register row
       python3 forward.py <data dir> status     # closed forward episodes of 60 (a count, never a return)
       python3 forward.py <data dir> decide     # once, when status says due: the decision + final verdict

<data dir>/term.csv is the recorder's table, exported on the node that runs ingestion:
  docker exec matrix-postgres psql -U matrix -d matrix -c \\
    "\\copy (SELECT currency, day, term FROM deribit_iv_daily ORDER BY 1, 2) TO STDOUT CSV HEADER" > <dir>/term.csv
Bybit klines, funding and books are fetched (cached under <dir>/cache) at run time.

The rule is matrix_shared.iv_term (the same code the recorder and the shadow module run;
it reproduces round 5's 118 D.3 entries exactly). Only the P&L of an episode lives here.
"""

import csv
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness_2026_10"))
import _path  # noqa: E402,F401

from matrix_shared import iv_term as T  # noqa: E402
from matrix_shared.research import Cell, Episode, NotCommitted, Spec, register  # noqa: E402
from matrix_shared.research.costs import round_trip_bps  # noqa: E402
from matrix_shared.research.fetch import PoliteFetcher, bybit_book, bybit_funding, bybit_klines  # noqa: E402
from matrix_shared.research.funding import funding_bps  # noqa: E402

UTC = timezone.utc
PREREG = "services/backtest/research/signal_2026_10_r5f/PREREG.md"
TAKER_BPS = 5.5
SIZE_USD = 5_000.0

SPEC = Spec(
    round="r5f",
    family="H14",
    title="forward test of round 5 D.3: front-end IV inversion -> long BTC/ETH perp 3 days",
    question=(
        "Round 5's near miss D.3 was positive in both historical splits (train +135.7 bps, median +166.4, "
        "n 48, t_wk 0.77; holdout +115.1, median +200.8, n 70, t_wk 1.61, record only) but failed the train "
        "gate. On data that did not exist when it was registered, does the frozen rule earn net > 0 at "
        "week-clustered t >= 2 over its first 60 episodes, and survive BHY at the programme's cumulative m?"
    ),
    cells=(
        Cell("D.3", {
            "rule": "pct(TERM) >= 0.90 -> long; TERM = median iv |delta| 0.40-0.60, DTE 1.5-10 minus DTE 45-120, "
                    ">= 5 trades each, Deribit option trades in [D-4h, D); pct over the asset's non-missing TERM "
                    "in the 365 calendar days before D, >= 120 values",
            "code": "matrix_shared.iv_term (decisions, term_features, percentile)",
            "assets": "BTCUSDT+ETHUSDT Bybit linear perp, pooled",
            "decision": "00:00 UTC daily; entry close of the 1h bar ending D+1h; exit close of the bar ending entry+72h",
            "episodes": "one per signal window (maximal run of consecutive firing days), entered on its first day; "
                        "a window starting while an episode of the same asset is open is skipped",
            "hold_h": 72,
            "threshold": 0.90,
            "cost": "2 x 5.5 bps taker + round-trip walk at $5k on Bybit books measured at decision time + funding",
            "n": 60,
        }),
    ),
    train=("2021-03-24", "2026-10-09"),  # round 5's two splits: the in-sample evidence, cited, not re-tested
    holdout=("2026-10-10", "2031-01-01"),  # forward window, by signal (decision) day
    forward_n=60,
    cluster="week",
    min_t=2.0,
    q_max=0.05,
    min_entry_lag_s=3600,
    data=(
        "TERM per (currency, decision day) from `deribit_iv_daily` (local tier, migration 0043), written by "
        "ingestion.iv_term_recorder from history.deribit.com get_last_trades_by_currency_and_time kind=option, "
        "the same endpoint, window and construction as round 5's fetch.py; the recorder backfills the 400 days "
        "before today so the first forward percentile has its full 365-day history, and refills missed days, "
        "so the evaluation reads a complete series. Bybit 1h klines and funding history fetched at evaluation."
    ),
    costs=(
        "net = price + funding - cost, bps of entry notional. price = P_exit / P_entry - 1 (long). funding: "
        "every Bybit settlement in (entry, exit], long pays a positive rate, notional marked to market "
        "(research.funding.funding_bps). cost = 11 bps taker + the median round-trip walk of 5 Bybit book "
        "snapshots 61 s apart per perp at $5k, taken when the decision runs (round 5: 0.012 / 0.040 bps)."
    ),
    survivorship="BTC and ETH perps only; both trade throughout. No universe selection.",
    notes=(
        "n = 60 is fixed here, before any forward data exists. D.3 fired ~21 times a year in 2021-2026 (118 "
        "episodes in 5.5 years, 6 in 2023), so 60 takes roughly 2-3 years. Power is low: round 5's pooled "
        "per-episode SD is 718 bps, so a true +125 bps mean gives an expected t of about 1.35 at n = 60 "
        "(P(t >= 2) roughly 0.25); +200 bps gives about 2.2. A pass is strong evidence; a fail does not rule "
        "out a smaller edge, and no re-run with a larger n under the same id is allowed. The shadow module `iv_inversion` trades the same signals on the shadow wallet; its "
        "fills are monitoring only (the shadow tracker reports `collecting` until 60). The decision is this "
        "harness evaluation, once, at n = 60; nothing is evaluated earlier and no early stop exists. If 60 "
        "episodes have not closed by 2031-01-01 the test is never decided (its family p stays 1)."
    ),
)


def load_term(path: Path) -> dict:
    """currency -> {day ms: term or None}."""
    out = {c: {} for c in T.CURRENCIES}
    with path.open() as fh:
        for r in csv.DictReader(fh):
            day = datetime.fromisoformat(r["day"]).replace(tzinfo=UTC)
            out[r["currency"]][T.to_ms(day)] = float(r["term"]) if r["term"] not in ("", None) else None
    return out


def spreads(f: PoliteFetcher) -> dict:
    out = {}
    for sym in T.CURRENCIES.values():
        rts = []
        for i in range(5):
            b = bybit_book(f, "linear", sym)
            rt = round_trip_bps(*b, SIZE_USD) if b else None
            if rt is not None:
                rts.append(rt)
            if i < 4:
                time.sleep(61)
        rts.sort()
        out[sym] = rts[len(rts) // 2] if rts else math.nan
    return out


def make_builder(data: Path, spread: dict):
    term = load_term(data / "term.csv")
    f = PoliteFetcher(data / "cache")
    now_ms = int(time.time() * 1000)

    def builder(cell, start, end):
        lo, hi = T.to_ms(start), min(T.to_ms(end), now_ms)
        for cur, sym in T.CURRENCIES.items():
            kl = bybit_klines(f, "linear", sym, "60", lo - T.DAY_MS, hi + 4 * T.DAY_MS)
            close = {int(k[0]) + T.H_MS: k[4] for k in kl}  # bar END ms -> close
            fund = [(datetime.fromtimestamp(ts / 1000, UTC), rate)
                    for ts, rate in bybit_funding(f, sym, lo, hi + 4 * T.DAY_MS)]
            for d in T.decisions(term[cur], lo, hi - 1):
                if not d["enter"]:
                    continue
                t0, t1 = d["entry_ms"], d["exit_ms"]
                p0, p1 = close.get(t0), close.get(t1)
                info = datetime.fromtimestamp(d["day"] / 1000, UTC)
                entry = datetime.fromtimestamp(t0 / 1000, UTC)
                exit_ = datetime.fromtimestamp(t1 / 1000, UTC)
                if p0 is None or p1 is None or t1 > now_ms:
                    yield Episode(sym, info, entry, exit_, math.nan)  # open (or unpriced) — the harness decides
                    continue
                price = (p1 / p0 - 1) * 1e4
                fbps, _ = funding_bps(fund, entry, exit_, 1, p0,
                                      lambda t: close.get(int(t.timestamp() * 1000)))
                cost = 2 * TAKER_BPS + spread.get(sym, 0.0)
                yield Episode(sym, info, entry, exit_, price + fbps - cost,
                              {"price": price, "funding": fbps, "cost": cost, "term": d["term"], "pct": d["pct"]})

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
        print(study.forward_status("D.3", make_builder(data, {})))
    elif mode == "decide":
        st = study.forward_status("D.3", make_builder(data, {}))
        if not st["due"]:
            sys.exit(f"not due: {st}")
        res = study.open_forward("D.3", make_builder(data, spreads(PoliteFetcher(data / "cache"))))
        print(res.row())
        print(study.finalise())

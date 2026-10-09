"""Round 3 (positive-funding mirror, 18 cells) re-run through matrix_shared.research.

usage: python3 round3_rerun.py <round1 data dir> <r3 work dir> [ledger path]

Proves the harness reproduces a hand-run round: same pre-registration (8346dbd, legacy,
no spec block), same frozen cell params as the ledger backfill, same cached data
(round 1's funding/klines, r3's spot_1h.pkl and books.pkl). The study is a *replicate*:
it runs against a temporary copy of the ledger and writes nothing to the real one.

Only the cell rule lives here (the builder). Window and information-time checks, one
sample per episode, exclusion of unpriced episodes, clustered t and p, the holdout
guards and BHY come from the harness.
"""

import shutil
import sys
import tempfile
from pathlib import Path

import _path  # noqa: F401
import numpy as np
import pandas as pd

from matrix_shared.research import Cell, Episode, HoldoutError, Ledger, Spec, register
from matrix_shared.research.funding import funding_bps

D, W = Path(sys.argv[1]), Path(sys.argv[2])
LEDGER = Path(sys.argv[3]) if len(sys.argv) > 3 else _path.ROOT / "docs/research/ledger.jsonl"
H = pd.Timedelta(hours=1)
XS, HOLDS = ("0.05", "0.08", "0.15"), (24, 48, 96)


def r3_params(X: str, h: int, flip: bool) -> dict:  # must equal the ledger's frozen params
    return {"signal": f"bybit settled funding >= {X} %", "legs": "short perp + long spot (Bybit, else Binance)",
            "entry": "S + 1h", "hold_h": h, "flip_exit": flip, "fees_bps": 31.0, "walk_usd_per_leg": 500,
            "borrow": "none"}


SPEC = Spec(
    round="r3", family="H1-mirror", title="positive-funding mirror of H1",
    question="After a settlement >= X, short perp + long spot for h hours (or flip exit): net > 0 out of sample?",
    cells=tuple(Cell(f"X{X}_h{h}{'_flip' if fl else ''}", r3_params(X, h, fl)) for X in XS for h in HOLDS for fl in (False, True)),
    train=("2025-10-01", "2026-06-01"), holdout=("2026-06-01", "2026-10-10"),
    cluster="week", min_t=2.0, min_entry_lag_s=3600,
)


def coin(sym):
    b = sym[:-4]
    for p in ("1000000", "100000", "10000", "1000"):
        if b.startswith(p) and len(b) > len(p):
            return b[len(p):], int(p)
    return b, 1


# ---- data (as round 3 research.py) ----------------------------------------
f = pd.read_csv(D / "funding.csv")
f["t"] = pd.to_datetime(f.ts, unit="ms", utc=True)
f = f.drop_duplicates(["symbol", "t"]).sort_values(["symbol", "t"])
syms = set(f[f.rate >= 0.0005].symbol)
bys = {s: g for s, g in f[f.symbol.isin(syms)].groupby("symbol")}
it = pd.read_csv(D / "kline_1h.csv", usecols=["symbol", "ts", "close"], chunksize=2_000_000)
k = pd.concat([c[c.symbol.isin(syms)] for c in it])
k["t"] = pd.to_datetime(k.ts, unit="ms", utc=True) + H  # bar END
P = k.pivot_table(index="t", columns="symbol", values="close", aggfunc="last").sort_index()
sp = pd.read_pickle(W / "spot_1h.pkl")
sp["t"] = pd.to_datetime(sp.ts, unit="ms", utc=True) + H
SP = {key: g.set_index("t").close.sort_index() for key, g in sp.groupby(["coin", "venue"])}
BOOK = pd.read_pickle(W / "books.pkl")["rows"]
SETTLE = {s: [(t.to_pydatetime(), r) for t, r in zip(g.t, g.rate.values)] for s, g in bys.items()}


def spot_at(c, t):
    for v in ("bybit", "binance"):
        s = SP.get((c, v))
        if s is not None and t in s.index:
            return v, s
    return None, None


def builder(cell: Cell, start, end):
    """Every signal of the cell in [start, end) that has both prices at entry and exit.
    Overlaps are NOT removed here: the harness does that."""
    X = float(cell.params["signal"].split(">= ")[1].split(" ")[0]) / 100
    hold, flip = cell.params["hold_h"], cell.params["flip_exit"]
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    for sym, g in bys.items():
        if sym not in P.columns:
            continue
        c, _ = coin(sym)
        px = P[sym]
        for r in g[(g.rate >= X) & (g.t >= a) & (g.t < b)].itertuples():
            S = r.t
            E = S.ceil("h") + H
            pe = px.get(E, np.nan)
            v, sg = spot_at(c, E)
            if not np.isfinite(pe) or sg is None:
                continue
            XX = E + hold * H
            if flip:
                w = g[(g.t > E) & (g.t <= E + hold * H) & (g.rate <= 0)]
                if len(w):
                    XX = w.t.iloc[0].ceil("h") + H
            pxx = px.get(XX, np.nan)
            if XX not in sg.index or not np.isfinite(pxx):
                continue
            fund, _n = funding_bps(SETTLE[sym], E.to_pydatetime(), XX.to_pydatetime(), perp_side=-1, entry_price=pe,
                                   price_at=lambda t, px=px: px.get(pd.Timestamp(t), None))
            hedge = (sg.at[XX] / sg.at[E] - 1) * 1e4 - (pxx / pe - 1) * 1e4
            bk = BOOK.get((sym, v))
            cost = bk["cost500"] if bk else float("nan")
            yield Episode(sym, S.to_pydatetime(), E.to_pydatetime(), XX.to_pydatetime(), fund + hedge - cost,
                          {"fund": fund, "hedge": hedge, "cost": cost})


# ---- run --------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp()) / "ledger.jsonl"
shutil.copy(LEDGER, tmp)
study = register(SPEC, "services/backtest/research/signal_2026_10_r3/PREREG.txt", ledger=Ledger(tmp), root=_path.ROOT,
                 legacy=True)
assert study.replicate, "round 3 is in the ledger; this must be a replicate"
train = study.evaluate(builder)
try:
    study.open_holdout(SPEC.cells[0].id, builder)
    raise SystemExit("guard failed: a train failure opened its decision holdout")
except HoldoutError as e:
    print("guard ok:", e)
record = {c.id: study.record_holdout(c.id, builder) for c in SPEC.cells}

ref = {s: pd.read_csv(W / f"cells_{s}.csv").set_index("cell") for s in ("train", "holdout")}
worst = 0.0
print(f"{'cell':16} {'split':7} {'n':>5} {'ref':>5} {'net':>8} {'ref':>8} {'t_wk':>7} {'ref':>7} {'t_day':>7} {'ref':>7} {'excl':>4} {'ref':>4}")
for split, res in (("train", train), ("holdout", {f"r3.{k}": v for k, v in record.items()})):
    for tid, r in res.items():
        c = tid.split(".", 1)[1]
        x = ref[split].loc[c]
        dn = abs(r.mean - x.net)
        worst = max(worst, dn, abs(r.t - x.t_wk) * 10)
        print(f"{c:16} {split:7} {r.n:5d} {int(x.n):5d} {r.mean:8.1f} {x.net:8.1f} {r.t:7.2f} {x.t_wk:7.2f} "
              f"{r.t_other:7.2f} {x.t_day:7.2f} {r.excluded:4d} {int(x.no_book):4d}")
        assert r.n == int(x.n) and r.excluded == int(x.no_book), (tid, split)
        assert abs(r.mean - x.net) <= 0.051 and abs(r.t - x.t_wk) <= 0.0051 and abs(r.t_other - x.t_day) <= 0.0051, (tid, split)
        assert not r.passed
print("final (replicate, nothing written):", {r["test_id"]: r["verdict"] for r in study.finalise()}.__len__(), "cells",
      "| ledger m", Ledger(tmp).m, "| real ledger untouched:", Ledger(LEDGER).m)
print("REPRODUCED: n and exclusions exact; net within 0.05 bps, t within 0.005, all 36 rows")

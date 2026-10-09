"""Round 3: positive-funding mirror of H1 (short perp + long spot after a settlement >= X). See PREREG.txt.

usage: python3 research.py <round1 data dir> <r3 work dir> train|holdout|all|ref
"ref" = reference rows, not counted in m: cash_and_carry as configured (>= 0.01 %, 8 h) and the 18 cells,
on the 25-coin active universe (round 1 active_universe.txt), both splits.
Needs <work>/spot_1h.pkl (fetch_spot.py) and <work>/books.pkl (fetch_books.py).
Writes <work>/eps_<split>.pkl (one row per cell x episode) and prints the cell table.
"""
import math, pickle, sys
from pathlib import Path

import numpy as np
import pandas as pd

D, W, WHICH = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
H = pd.Timedelta(hours=1)
SPLITS = {"train": (pd.Timestamp("2025-10-01", tz="UTC"), pd.Timestamp("2026-06-01", tz="UTC")),
          "holdout": (pd.Timestamp("2026-06-01", tz="UTC"), pd.Timestamp("2026-10-10", tz="UTC"))}
XS = (0.0005, 0.0008, 0.0015)
HOLDS = (24, 48, 96)
FEES = 31.0
LEG = 500.0


def coin(sym):
    b = sym[:-4]
    for p in ("1000000", "100000", "10000", "1000"):
        if b.startswith(p) and len(b) > len(p):
            return b[len(p):], int(p)
    return b, 1


# ---- data ---------------------------------------------------------------
f = pd.read_csv(D / "funding.csv")
f["t"] = pd.to_datetime(f.ts, unit="ms", utc=True)
f = f.drop_duplicates(["symbol", "t"]).sort_values(["symbol", "t"])
active = set(open(D.parent / "active_universe.txt").read().split())
syms = active if WHICH == "ref" else set(f[f.rate >= min(XS)].symbol)
bys = {s: g for s, g in f[f.symbol.isin(syms)].groupby("symbol")}

it = pd.read_csv(D / "kline_1h.csv", usecols=["symbol", "ts", "high", "close", "turnover"], chunksize=2_000_000)
k = pd.concat([c[c.symbol.isin(syms)] for c in it])
k["t"] = pd.to_datetime(k.ts, unit="ms", utc=True) + H  # bar END
P = k.pivot_table(index="t", columns="symbol", values="close", aggfunc="last").sort_index()
PH = k.pivot_table(index="t", columns="symbol", values="high", aggfunc="last").reindex(P.index)
TO = k.pivot_table(index="t", columns="symbol", values="turnover", aggfunc="last").reindex(P.index)
it = pd.read_csv(D / "premium_1h.csv", usecols=["symbol", "ts", "close"], chunksize=2_000_000)
pr = pd.concat([c[c.symbol.isin(syms)] for c in it])
pr["t"] = pd.to_datetime(pr.ts, unit="ms", utc=True) + H
PR = pr.pivot_table(index="t", columns="symbol", values="close", aggfunc="last")

sp = pd.read_pickle(W / "spot_1h.pkl")
sp["t"] = pd.to_datetime(sp.ts, unit="ms", utc=True) + H
SP = {key: g.set_index("t").sort_index() for key, g in sp.groupby(["coin", "venue"])}

B = pickle.load(open(W / "books.pkl", "rb"))
BOOK = B["rows"]  # {(perp_sym, venue): {"cost500":..., "cost5k":...}}


def spot_at(c, t):
    for v in ("bybit", "binance"):
        g = SP.get((c, v))
        if g is not None and t in g.index:
            return v, g
    return None, None


# ---- episodes -----------------------------------------------------------
def episodes(X, hold, flip, a, b):
    out = []
    for sym, g in bys.items():
        if sym not in P.columns:
            continue
        c, mult = coin(sym)
        busy = None
        for r in g[(g.rate >= X) & (g.t >= a) & (g.t < b)].itertuples():
            S = r.t
            if busy is not None and S < busy:
                continue
            E = S.ceil("h") + H
            pe = P[sym].get(E, np.nan)
            v, sg = spot_at(c, E)
            if not np.isfinite(pe) or sg is None:
                continue
            XX = E + hold * H
            if flip:
                w = g[(g.t > E) & (g.t <= E + hold * H) & (g.rate <= 0)]
                if len(w):
                    XX = w.t.iloc[0].ceil("h") + H
            px = P[sym].get(XX, np.nan)
            if XX not in sg.index or not np.isfinite(px):
                continue
            busy = XX
            se, sx = sg.at[E, "close"], sg.at[XX, "close"]
            fs = g[(g.t > E) & (g.t <= XX)]
            mtm = P[sym].reindex(fs.t.dt.ceil("h")).values / pe
            mtm = np.where(np.isfinite(mtm), mtm, 1.0)
            fund = float((fs.rate.values * mtm).sum()) * 1e4
            rp, rs = (px / pe - 1) * 1e4, (sx / se - 1) * 1e4
            hedge = rs - rp
            # hourly path within the hold
            hrs = pd.date_range(E + H, XX, freq="h")
            pp = P[sym].reindex(hrs).values / pe - 1
            ss = sg.close.reindex(hrs).values / se - 1
            hp = (ss - pp) * 1e4
            cumf = np.zeros(len(hrs))
            if len(fs):
                ft = fs.t.dt.ceil("h").values
                contrib = fs.rate.values * mtm * 1e4
                for j, tt in enumerate(hrs.values):
                    cumf[j] = contrib[ft <= tt].sum()
            mark = hp + cumf
            hi = PH[sym].reindex(hrs).values
            bk = BOOK.get((sym, v))
            out.append(dict(
                sym=sym, coin=c, venue=v, S=S, E=E, X=XX, hours=(XX - E) / H, rate0=r.rate * 1e4,
                fund=fund, hedge=hedge, rp=rp, rs=rs, nset=len(fs),
                basis_entry=(pe / (se * mult) - 1) * 1e4, prem_S=PR[sym].get(S.ceil("h"), np.nan) * 1e4 if sym in PR else np.nan,
                worst_hedge=np.nanmin(hp) if np.isfinite(hp).any() else np.nan,
                worst_mark=np.nanmin(mark) if np.isfinite(mark).any() else np.nan,
                runup=(np.nanmax(hi) / pe - 1) * 1e4 if np.isfinite(hi).any() else np.nan,
                turn24=TO[sym].loc[E - 23 * H:E].sum(),
                cost500=bk["cost500"] if bk else np.nan, cost5k=bk["cost5k"] if bk else np.nan,
                active=sym in active))
    e = pd.DataFrame(out)
    if len(e):
        e["net"] = e.fund + e.hedge - e.cost500
        e["net5k"] = e.fund + e.hedge - e.cost5k
        e["netfee2"] = e.net - FEES
        e["day"] = e.S.dt.floor("D")
        e["week"] = e.S.dt.tz_localize(None).dt.to_period("W").astype(str)
    return e


# ---- statistics ---------------------------------------------------------
def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / d if abs(d) > 1e-30 else 1e30
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d; c = 1 + aa / c; d = 1 / d if abs(d) > 1e-30 else 1e30; h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d; c = 1 + aa / c; d = 1 / d if abs(d) > 1e-30 else 1e30
        de = d * c; h *= de
        if abs(de - 1) < 1e-12:
            break
    return h


def t_sf(t, df):
    """One-sided P(T >= t) for Student t."""
    x = df / (df + t * t)
    lb = math.lgamma(df / 2 + 0.5) - math.lgamma(df / 2) - math.lgamma(0.5)
    bt = math.exp(lb + (df / 2) * math.log(x) + 0.5 * math.log(1 - x))
    ib = bt * _betacf(df / 2, 0.5, x) / (df / 2) if x < (df / 2 + 1) / (df / 2 + 0.5 + 2) else 1 - bt * _betacf(0.5, df / 2, 1 - x) / 0.5
    return ib / 2 if t > 0 else 1 - ib / 2


def st(v, cl):
    v = np.asarray(v, float); m = np.isfinite(v); v = v[m]; n = len(v)
    if n < 3:
        return dict(n=n, mean=np.nan, med=np.nan, t=np.nan, G=0, p=np.nan)
    mu = v.mean(); c = np.asarray(cl)[m]
    s = pd.Series(v - mu).groupby(c).sum(); G = len(s)
    se = math.sqrt((s ** 2).sum() * G / (G - 1)) / n if G > 1 else np.nan
    t = mu / se if se and se > 0 else np.nan
    return dict(n=n, mean=round(mu, 1), med=round(float(np.median(v)), 1), t=round(t, 2), G=G,
                p=t_sf(t, G - 1) if np.isfinite(t) else np.nan)


def trim(v, q=0.95):
    v = pd.Series(v).dropna()
    return v[v < v.quantile(q)]


def cell_rows(e0, name):
    nb = int(e0.cost500.isna().sum()) if len(e0) else 0
    e = e0[e0.cost500.notna()] if len(e0) else e0
    if not len(e):
        return dict(cell=name, n=0)
    w, d = st(e.net, e.week), st(e.net, e.day)
    x = e.net.dropna(); top = x.sort_values(ascending=False)
    kept = e[e.net < e.net.quantile(0.95)]
    return dict(cell=name, n=w["n"], net=w["mean"], med=w["med"], t_wk=w["t"], t_day=d["t"], p_wk=w["p"],
                trim5=round(trim(e.net).mean(), 1), ex_top5=st(kept.net, kept.week)["mean"], ex_top5_t=st(kept.net, kept.week)["t"],
                fund=round(e.fund.mean(), 1), hedge=round(e.hedge.mean(), 1), cost=round(e.cost500.mean(), 1),
                net5k=st(e.net5k, e.week)["mean"], netfee2=st(e.netfee2, e.week)["mean"],
                top5pct_share=round(top.head(max(1, len(top) // 20)).sum() / x.sum(), 2) if x.sum() else np.nan,
                coins=e.sym.nunique(), no_book=nb)


def ref():
    rows = []
    cells = [(0.0001, 8, False, "cash_and_carry as configured: X0.01_h8"), (0.0008, 48, False, "cash_and_carry v4 defaults: X0.08_h48")]
    cells += [(X, h, fl, f"X{X*100:.2f}_h{h}{'_flip' if fl else ''}") for X in XS for h in HOLDS for fl in (False, True)]
    for sp_, (a, b) in SPLITS.items():
        for X, h, fl, name in cells:
            e = episodes(X, h, fl, a, b)
            rows.append(dict(split=sp_, **cell_rows(e, name)))
    r = pd.DataFrame(rows)
    r.to_csv(W / "cells_ref_active.csv", index=False)
    print(r[["split", "cell", "n", "net", "med", "t_wk", "fund", "hedge", "cost", "coins", "no_book"]].to_string(index=False))


def main():
    if WHICH == "ref":
        return ref()
    splits = ["train", "holdout"] if WHICH == "all" else [WHICH]
    for sp_ in splits:
        a, b = SPLITS[sp_]
        allrows, eps = [], []
        for X in XS:
            for hold in HOLDS:
                for flip in (False, True):
                    name = f"X{X*100:.2f}_h{hold}{'_flip' if flip else ''}"
                    e = episodes(X, hold, flip, a, b)
                    e["cell"] = name
                    eps.append(e)
                    allrows.append(cell_rows(e, name))
                    print(sp_, name, "done", flush=True)
        E = pd.concat(eps, ignore_index=True)
        E.to_pickle(W / f"eps_{sp_}.pkl")
        r = pd.DataFrame(allrows)
        r.to_csv(W / f"cells_{sp_}.csv", index=False)
        print(sp_); print(r.to_string(index=False))


if __name__ == "__main__":
    main()

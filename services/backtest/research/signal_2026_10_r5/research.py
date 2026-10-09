"""Round 5: options-implied signals on BTC/ETH perps. Rules: PREREG.txt (frozen in 1b2b6e1).

usage: python3 research.py <data dir> features   # daily feature table -> features.pkl
       python3 research.py <data dir> train      # decision on TRAIN, writes train_pass.json
       python3 research.py <data dir> holdout    # train passers only, once
       python3 research.py <data dir> record     # every cell, both splits, descriptive extras (after the decision)
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(sys.argv[1])
MODE = sys.argv[2]
H = 3_600_000
DAY = 24 * H
TRAIN = (datetime(2021, 3, 24, tzinfo=timezone.utc), datetime(2024, 7, 1, tzinfo=timezone.utc))
HOLD = (datetime(2024, 7, 1, tzinfo=timezone.utc), datetime(2026, 10, 9, tzinfo=timezone.utc))
ASSETS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
TAKER = 5.5
SIZE = 5_000.0
MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}


# ---------------------------------------------------------------- data
def closes(sym):
    k = json.loads((D / f"kline_{sym}.json").read_text())
    # key = bar END time (start + 1h); value = close
    return pd.Series({int(r[0]) + H: float(r[4]) for r in k}).sort_index()


def funding(sym):
    f = json.loads((D / f"funding_{sym}.json").read_text())
    return pd.Series({int(r["fundingRateTimestamp"]): float(r["fundingRate"]) for r in f}).sort_index()


def dvol(cur):
    rows = json.loads((D / f"dvol_{cur}.json").read_text())
    return pd.Series({int(r[0]) + H: float(r[4]) for r in rows}).sort_index()  # bar end -> close


def parse(name):
    _, exp, strike, typ = name.split("-")
    d, mon, yy = int(exp[:-5]), MON[exp[-5:-2]], 2000 + int(exp[-2:])
    return datetime(yy, mon, d, 8, tzinfo=timezone.utc).timestamp() * 1000, float(strike), typ


def ncdf(x):
    return 0.5 * (1 + np.vectorize(math.erf)(x / math.sqrt(2)))


def option_features(cur, day_ms):
    """Features from the trades in [day_ms - 4h, day_ms) — the file of the previous day."""
    f = D / "trades" / f"{cur}_{datetime.fromtimestamp((day_ms - DAY) / 1000, timezone.utc):%Y%m%d}.json"
    if not f.exists():
        return None
    tr = json.loads(f.read_text())
    if not tr:
        return {"n": 0, "put": 0.0, "call": 0.0, "RR25": np.nan, "TERM": np.nan}
    df = pd.DataFrame(tr, columns=["ts", "name", "iv", "idx", "amt", "id"])
    df = df[(df.ts >= day_ms - 4 * H) & (df.ts < day_ms)]
    p = df.name.map(parse)
    df["exp"] = [x[0] for x in p]
    df["K"] = [x[1] for x in p]
    df["typ"] = [x[2] for x in p]
    df["notional"] = df.amt * df.idx
    out = {"n": len(df), "put": df.notional[df.typ == "P"].sum(), "call": df.notional[df.typ == "C"].sum()}
    v = df[df.iv.notna() & (df.iv > 0)].copy()
    v["T"] = (v.exp - v.ts) / (365 * DAY)
    v = v[v["T"] > 0]
    v["dte"] = v["T"] * 365
    sig = v.iv / 100
    d1 = (np.log(v.idx / v.K) + 0.5 * sig ** 2 * v["T"]) / (sig * np.sqrt(v["T"]))
    v["delta"] = np.where(v.typ == "C", ncdf(d1.values), ncdf(d1.values) - 1)
    c = v[(v.typ == "C") & v.delta.between(0.15, 0.35) & v.dte.between(20, 45)].iv
    pu = v[(v.typ == "P") & v.delta.between(-0.35, -0.15) & v.dte.between(20, 45)].iv
    out["RR25"] = c.median() - pu.median() if len(c) >= 5 and len(pu) >= 5 else np.nan
    atm = v[v.delta.abs().between(0.40, 0.60)]
    fr, bk = atm[atm.dte.between(1.5, 10)].iv, atm[atm.dte.between(45, 120)].iv
    out["TERM"] = fr.median() - bk.median() if len(fr) >= 5 and len(bk) >= 5 else np.nan
    return out


def features():
    rows = []
    for cur, sym in ASSETS.items():
        px, dv = closes(sym), dvol(cur)
        lr = np.log(px).diff()
        day = int(TRAIN[0].timestamp() * 1000)
        end = int(HOLD[1].timestamp() * 1000)
        while day < end:
            r = {"asset": cur, "D": day}
            r["DVOL"] = dv.get(day, np.nan)
            prev = dv.get(day - DAY, np.nan)
            r["DVOLchg"] = math.log(r["DVOL"] / prev) if prev == prev and r["DVOL"] == r["DVOL"] else np.nan
            w = lr[(lr.index > day - 720 * H) & (lr.index <= day)].dropna()
            r["RV30"] = w.std() * math.sqrt(8760) * 100 if len(w) >= 700 else np.nan
            r["VRP"] = r["DVOL"] - r["RV30"]
            p0, p1 = px.get(day - DAY, np.nan), px.get(day, np.nan)
            r["ret24"] = p1 / p0 - 1
            o = option_features(cur, day)
            if o is None:
                r.update(n=np.nan, put=np.nan, call=np.nan, RR25=np.nan, TERM=np.nan)
            else:
                r.update(o)
            rows.append(r)
            day += DAY
    f = pd.DataFrame(rows)
    out = []
    for cur, g in f.groupby("asset"):
        g = g.sort_values("D").reset_index(drop=True)
        ok = g.n >= 20
        put = g.put.where(ok)
        call = g.call.where(ok)
        g["PC3"] = put.rolling(3).sum() / call.rolling(3).sum()  # NaN if any of the three windows is thin
        for x in ("VRP", "DVOLchg", "RR25", "TERM", "PC3"):
            vals = g[x].values
            pct = np.full(len(g), np.nan)
            for i in range(len(g)):
                if vals[i] != vals[i]:
                    continue
                lo = g.D.values[i] - 365 * DAY
                hist = vals[(g.D.values >= lo) & (g.D.values < g.D.values[i])]
                hist = hist[~np.isnan(hist)]
                if len(hist) >= 120:
                    pct[i] = (hist < vals[i]).mean()
            g[f"pct_{x}"] = pct
        out.append(g)
    f = pd.concat(out, ignore_index=True)
    f.to_pickle(D / "features.pkl")
    print(f.groupby("asset")[["DVOL", "RV30", "RR25", "TERM", "PC3"]].count())


# ---------------------------------------------------------------- cells
def cond(cell, r):
    """-> side (+1 long, -1 short) or 0."""
    fam = cell.split(".")[0]
    if fam == "A":
        if cell.startswith("A.hi"):
            return 1 if r.pct_VRP >= 0.90 else 0
        return -1 if r.pct_VRP <= 0.10 else 0
    if fam == "B":
        if cell.startswith("B.put"):
            return 1 if r.pct_RR25 <= 0.10 else 0
        return -1 if r.pct_RR25 >= 0.90 else 0
    if fam == "C":
        if r.pct_DVOLchg >= 0.95 and r.ret24 == r.ret24 and r.ret24 != 0:
            return -1 if r.ret24 > 0 else 1
        return 0
    if fam == "D":
        return 1 if r.pct_TERM >= 0.90 else 0
    if fam == "P":
        return 1 if r.pct_PC3 >= 0.90 else 0
    raise ValueError(cell)


CELLS = ["A.hi.3", "A.hi.7", "A.lo.3", "A.lo.7", "B.put.3", "B.put.7", "B.call.3", "B.call.7",
         "C.3", "C.7", "D.3", "D.7", "P.3", "P.7"]


def spread_rt():
    out = {}
    for sym in ASSETS.values():
        rts = []
        for i in range(5):
            b = json.loads((D / f"book_{sym}_{i}.json").read_text())
            bids = [(float(p), float(q)) for p, q in b["b"]]
            asks = [(float(p), float(q)) for p, q in b["a"]]
            mid = (bids[0][0] + asks[0][0]) / 2

            def walk(levels):
                left, cost = SIZE, 0.0
                for p, q in levels:
                    take = min(left, p * q)
                    cost += take * abs(p / mid - 1)
                    left -= take
                    if left <= 0:
                        break
                return cost / SIZE * 1e4

            rts.append(walk(asks) + walk(bids))
        out[sym] = float(np.median(rts))
    return out


def episodes(f, cell, px, fund, spr, sym, asset):
    hold = int(cell.split(".")[-1]) * DAY
    g = f[f.asset == asset].sort_values("D")
    eps, open_until, prev_on, skip_window = [], -1, False, False
    for r in g.itertuples():
        side = cond(cell, r)
        on = side != 0
        if on and not prev_on:  # a new signal window starts
            skip_window = r.D < open_until
            if not skip_window:
                t0 = r.D + H
                t1 = t0 + hold
                if t0 in px.index and t1 in px.index:
                    p0, p1 = px[t0], px[t1]
                    price = side * (p1 / p0 - 1) * 1e4
                    fs = fund[(fund.index > t0) & (fund.index <= t1)]
                    fund_bps = sum(-side * rate * (px.get(ts, p0) / p0) * 1e4 for ts, rate in fs.items())
                    cost = 2 * TAKER + spr[sym]
                    eps.append({"cell": cell, "asset": asset, "entry": t0, "side": side, "price": price,
                                "funding": fund_bps, "gross": price + fund_bps, "cost": cost,
                                "net": price + fund_bps - cost})
                    open_until = t1
        prev_on = on
    return eps


def clustered(values, keys):
    x = np.asarray(values, float)
    n = len(x)
    if n < 2:
        return {"n": n, "mean": float(x.mean()) if n else np.nan, "t": np.nan, "G": n, "p": 1.0}
    mu = x.mean()
    sums = pd.Series(x - mu).groupby(list(keys)).sum()
    G = len(sums)
    if G < 2:
        return {"n": n, "mean": mu, "t": np.nan, "G": G, "p": 1.0}
    se = math.sqrt((sums ** 2).sum() * G / (G - 1)) / n
    t = mu / se if se > 0 else np.nan
    p = t_sf(t, G - 1) if t == t else 1.0
    return {"n": n, "mean": mu, "t": t, "G": G, "p": p}


def t_sf(t, df):
    """One-sided Student-t upper tail by numeric integration of the density (no scipy on the host)."""
    if t < 0:
        return 1.0 - t_sf(-t, df)
    x = np.linspace(t, t + 400.0, 400_001)
    logc = math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi)
    pdf = np.exp(logc - (df + 1) / 2 * np.log1p(x * x / df))
    return float(np.sum((pdf[1:] + pdf[:-1]) / 2 * np.diff(x)))


def wk(ms_):
    iso = datetime.fromtimestamp(ms_ / 1000, timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def all_episodes():
    f = pd.read_pickle(D / "features.pkl")
    spr = spread_rt()
    eps = []
    for asset, sym in ASSETS.items():
        px, fund = closes(sym), funding(sym)
        for c in CELLS:
            eps += episodes(f, c, px, fund, spr, sym, asset)
    e = pd.DataFrame(eps)
    e["split"] = np.where(e.entry < HOLD[0].timestamp() * 1000, "train", "holdout")
    return e, spr


def summarize(e):
    rows = []
    for c in CELLS:
        for sp in ("train", "holdout"):
            x = e[(e.cell == c) & (e.split == sp)]
            s = clustered(x.net, [wk(t) for t in x.entry])
            rows.append({"cell": c, "split": sp, "n": s["n"], "G": s["G"], "net": round(s["mean"], 1),
                         "t_wk": round(s["t"], 2) if s["t"] == s["t"] else None, "p": s["p"],
                         "gross": round(x.gross.mean(), 1) if len(x) else None,
                         "median": round(x.net.median(), 1) if len(x) else None,
                         "funding": round(x.funding.mean(), 1) if len(x) else None})
    return pd.DataFrame(rows)


if MODE == "features":
    features()
elif MODE == "train":
    e, spr = all_episodes()
    print("round-trip spread bps at $5k:", spr)
    s = summarize(e[e.split == "train"])
    s = s[s.split == "train"]
    print(s.to_string(index=False))
    passers = s[(s.net > 0) & (s.t_wk >= 2)].cell.tolist()
    (D / "train_pass.json").write_text(json.dumps({"passers": passers, "table": s.to_dict("records")}))
    print("train passers:", passers)
elif MODE == "holdout":
    passers = json.loads((D / "train_pass.json").read_text())["passers"]
    e, _ = all_episodes()
    s = summarize(e[e.cell.isin(passers)])
    s = s[(s.split == "holdout") & s.cell.isin(passers)]
    print(s.to_string(index=False))
    (D / "holdout.json").write_text(json.dumps(s.to_dict("records")))
elif MODE == "record":
    e, spr = all_episodes()
    e.to_pickle(D / "episodes.pkl")
    s = summarize(e)
    print(s.to_string(index=False))
    s.to_csv(D / "cells_all.csv", index=False)

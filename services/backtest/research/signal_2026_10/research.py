"""Signal research 2026-10: pre-registered families on 1y Bybit history (see PREREG.md).

usage: python3 research.py train|holdout [H...]
Writes results/<split>.json: per hypothesis the per-period net returns (bps) and summary.
"""
import json, math, sys
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)
H = pd.Timedelta(hours=1)
TRAIN = (pd.Timestamp("2025-10-01", tz="UTC"), pd.Timestamp("2026-06-01", tz="UTC"))
HOLD = (pd.Timestamp("2026-06-01", tz="UTC"), pd.Timestamp("2026-10-10", tz="UTC"))
LEG = 7.5e-4  # one entry or one exit, directional
TOPN = 100


def _ts(s):
    return pd.to_datetime(s.astype("int64"), unit="ms", utc=True)


def load():
    k = pd.read_csv(D / "kline_1h.csv")
    k["t"] = _ts(k.ts) + H  # value known at bar END
    P = k.pivot_table(index="t", columns="symbol", values="close", aggfunc="last").sort_index()
    TO = k.pivot_table(index="t", columns="symbol", values="turnover", aggfunc="last").reindex(P.index)
    pr = pd.read_csv(D / "premium_1h.csv")
    pr["t"] = _ts(pr.ts) + H
    PR = pr.pivot_table(index="t", columns="symbol", values="close", aggfunc="last").reindex(P.index)
    f = pd.read_csv(D / "funding.csv")
    f["t"] = _ts(f.ts)
    f = f.sort_values(["symbol", "t"]).drop_duplicates(["symbol", "t"])
    f["gap_h"] = f.groupby("symbol").t.diff().dt.total_seconds() / 3600
    f["gap_h"] = f.gap_h.fillna(8).clip(1, 8)
    f["r8"] = f.rate * 8 / f.gap_h
    F = f.pivot_table(index="t", columns="symbol", values="rate", aggfunc="last")
    F8 = f.pivot_table(index="t", columns="symbol", values="r8", aggfunc="last")
    oi = None
    if (D / "oi_1h.csv").exists():
        o = pd.read_csv(D / "oi_1h.csv")
        o["t"] = _ts(o.ts)
        oi = o.pivot_table(index="t", columns="symbol", values="oi", aggfunc="last").sort_index()
    return P, TO, PR, F, F8, f, oi


class Ctx:
    def __init__(self):
        self.P, self.TO, self.PR, self.F, self.F8, self.fl, self.OI = load()
        self.vol7 = self.TO.rolling(168, min_periods=150).sum()
        first = self.P.apply(lambda c: c.first_valid_index())
        self.first = first
        # cumulative funding per symbol on settlement index, for (a, b] sums
        self.Fc = self.F.fillna(0).cumsum()
        self.bysym = {k: g.sort_values("t") for k, g in self.fl.groupby("symbol")}

    def universe(self, T, n=TOPN):
        if T not in self.vol7.index:
            return []
        v = self.vol7.loc[T].dropna()
        ok = self.first[v.index] <= T - pd.Timedelta(days=30)
        v = v[ok & self.P.loc[T, v.index].notna()]
        return list(v.sort_values(ascending=False).index[:n])

    def px(self, T, syms):
        return self.P.loc[T, syms] if T in self.P.index else pd.Series(np.nan, index=syms)

    def funding_sum(self, syms, a, b):
        """Sum of settled rates with a < S <= b (fraction of notional, + = longs pay)."""
        idx = self.Fc.index
        ia = idx.searchsorted(a, side="right") - 1
        ib = idx.searchsorted(b, side="right") - 1
        cols = [s for s in syms if s in self.Fc.columns]
        hi = self.Fc.iloc[ib][cols] if ib >= 0 else 0.0
        lo = self.Fc.iloc[ia][cols] if ia >= 0 else 0.0
        return (hi - lo).reindex(syms).fillna(0.0)


def xs(ctx, times, score, hold_h, *, frac=0.1, skip_h=1, long_low=True, pick=None):
    """Long/short decile portfolio. score(T, U) -> Series (higher = short when long_low)."""
    out, prevL, prevS = [], set(), set()
    for T in times:
        U = ctx.universe(T)
        if len(U) < 30:
            continue
        if pick is not None:
            L, S = pick(T, U)
        else:
            s = score(T, U).dropna()
            if len(s) < 30:
                continue
            k = max(1, int(round(frac * len(s))))
            s = s.sort_values()
            lo, hi = list(s.index[:k]), list(s.index[-k:])
            L, S = (lo, hi) if long_low else (hi, lo)
        if not L or not S:
            continue
        E, X = T + skip_h * H, T + (skip_h + hold_h) * H
        pe, px_ = ctx.px(E, L + S), ctx.px(X, L + S)
        r = (px_ / pe - 1).dropna()
        L2, S2 = [x for x in L if x in r.index], [x for x in S if x in r.index]
        if not L2 or not S2:
            continue
        fs = ctx.funding_sum(L2 + S2, E, X)
        gross = r[L2].mean() - r[S2].mean()
        fund = -fs[L2].mean() + fs[S2].mean()
        n = (len(L2) + len(S2)) / 2
        changes = len(set(L2) ^ prevL) + len(set(S2) ^ prevS)
        cost = LEG * changes / n
        prevL, prevS = set(L2), set(S2)
        out.append({"T": str(T), "gross": gross * 1e4, "fund": fund * 1e4, "cost": cost * 1e4,
                    "net": (gross + fund - cost) * 1e4})
    # final exit cost
    return out


def times(start, end, every_h, hour=0):
    t0 = start.normalize() + pd.Timedelta(hours=hour)
    return list(pd.date_range(t0, end - H, freq=f"{every_h}h"))


# ---- hypotheses ---------------------------------------------------------
def h2(ctx, split, hold_h):
    a, b = split

    def score(T, U):
        F8 = ctx.F8.loc[T - 24 * H:T].reindex(columns=U)
        return F8.ffill().iloc[-1]

    return xs(ctx, times(a, b, hold_h), score, hold_h)


def h3(ctx, split):
    a, b = split

    def score(T, U):
        return ctx.PR.loc[T - 23 * H:T].reindex(columns=U).mean()

    return xs(ctx, times(a, b, 24), score, 24)


def h4(ctx, split):
    a, b = split
    OI = ctx.OI

    def pick(T, U):
        U = [u for u in U if u in OI.columns]
        t1 = OI.index.searchsorted(T - H, side="right") - 1
        t0 = OI.index.searchsorted(T - 25 * H, side="right") - 1
        if t0 < 0:
            return [], []
        d = np.log(OI.iloc[t1][U].astype(float) / OI.iloc[t0][U].astype(float)).replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) < 30:
            return [], []
        top = list(d.sort_values().index[-max(2, int(round(0.2 * len(d)))):])
        ret = (ctx.P.loc[T, top] / ctx.P.loc[T - 24 * H, top] - 1).dropna().sort_values()
        h = len(ret) // 2
        return list(ret.index[:h]), list(ret.index[-h:])

    return xs(ctx, times(a, b, 24), None, 24, pick=pick)


def h5(ctx, split):
    a, b = split
    return xs(ctx, times(a, b, 24), lambda T, U: ctx.P.loc[T, U] / ctx.P.loc[T - 24 * H, U] - 1, 24)


def h6(ctx, split):
    a, b = split
    start = a + pd.Timedelta(days=(7 - a.weekday()) % 7)  # Monday 00 UTC
    return xs(ctx, times(start, b, 168), lambda T, U: ctx.P.loc[T - 24 * H, U] / ctx.P.loc[T - 192 * H, U] - 1, 168,
              long_low=False)


def basket(ctx, a, b):
    """Equal-weight hourly return of the top-20 universe, re-picked daily."""
    rows = []
    for T in times(a, b, 24):
        U = ctx.universe(T, 20)
        if len(U) < 10:
            continue
        seg = ctx.P.loc[T:T + 24 * H, U]
        r = seg.pct_change().iloc[1:].mean(axis=1)
        rows.append(r)
    return pd.concat(rows)


def h7(ctx, split, sides=None):
    a, b = split
    r = basket(ctx, a, b)
    res = {}
    # day of week: 00->24 UTC compounding of the hourly basket; known at T+24h
    day = (1 + r).groupby((r.index - H).floor("D")).prod() - 1
    for dow in range(7):
        x = day[day.index.weekday == dow] * 1e4
        res[f"H7.dow{dow}"] = x
    hour = r * 1e4
    for h in range(24):
        res[f"H7.hod{h:02d}"] = hour[(hour.index - H).hour == h]
    out = {}
    for k, x in res.items():
        side = sides[k] if sides else (1 if x.mean() >= 0 else -1)
        net = side * x - 15.0
        out[k] = ([{"T": str(t), "net": v, "gross": side * g, "fund": 0.0, "cost": 15.0}
                   for t, v, g in zip(x.index, net.values, x.values)], side)
    return out


# ---- H1 inverse carry ---------------------------------------------------
def base_coin(sym):
    b = sym[:-4]
    for p in ("1000000", "100000", "10000", "1000"):
        if b.startswith(p) and len(b) > len(p):
            return b[len(p):]
    return b


def borrow_table():
    sp = D
    by = json.load(open(sp / "margin_data.json"))
    rates = {}
    for v in by["result"]["vipCoinList"]:
        for c in v["list"]:
            if c["borrowable"] and c["hourlyBorrowRate"]:
                rates[c["currency"]] = float(c["hourlyBorrowRate"])
    bn = json.load(open(sp / "bn_margin.json"))["data"]
    for x in bn:
        r = float(x["specs"][0]["dailyInterestRate"]) / 24
        a = x["assetName"]
        rates[a] = min(rates.get(a, r), r)
    return rates


def h1(ctx, split, *, include_signal_settlement=False, thresh=-0.0008, hold_h=48, flip_exit=False):
    a, b = split
    rates = borrow_table()
    ev = []
    fl = ctx.fl[(ctx.fl.t >= a) & (ctx.fl.t < b) & (ctx.fl.rate <= thresh)].sort_values("t")
    busy = {}
    for row in fl.itertuples():
        sym, S = row.symbol, row.t
        if busy.get(sym) and S < busy[sym]:
            continue
        coin = base_coin(sym)
        executable = coin in rates
        X = S + hold_h * H
        gs = ctx.bysym[sym]
        g = gs[(gs.t > S - (H if include_signal_settlement else pd.Timedelta(0))) & (gs.t <= X)]
        if flip_exit:
            stop = g[(g.rate >= 0) & (g.t > S)]
            if len(stop):
                X = stop.t.iloc[0]
                g = g[g.t <= X]
        if g.empty and X > ctx.fl.t.max():
            continue
        fund = -g.rate.sum()
        pe = ctx.PR[sym].get(S, np.nan) if sym in ctx.PR else np.nan
        pxx = ctx.PR[sym].get(X.floor("h"), np.nan) if sym in ctx.PR else np.nan
        basis = (pxx - pe) if np.isfinite(pe) and np.isfinite(pxx) else 0.0
        hours = (X - S).total_seconds() / 3600
        br = rates.get(coin, np.nan) * hours
        busy[sym] = S + hold_h * H
        ev.append({"T": str(S), "sym": sym, "exec": executable, "rate0": row.rate, "fund": fund * 1e4,
                   "basis": basis * 1e4, "borrow": br * 1e4, "hours": hours,
                   "net0": (fund + basis - 0.003) * 1e4,
                   "net": (fund + basis - 0.003 - (br if np.isfinite(br) else 0)) * 1e4,
                   "net3": (fund + basis - 0.003 - 3 * (br if np.isfinite(br) else 0)) * 1e4})
    return ev


def summ(xs_, key="net"):
    v = np.array([x[key] for x in xs_], dtype=float)
    v = v[np.isfinite(v)]
    n = len(v)
    if n < 2:
        return {"n": n}
    m, sd = v.mean(), v.std(ddof=1)
    return {"n": n, "mean": round(m, 2), "sd": round(sd, 1), "t": round(m / sd * math.sqrt(n), 2)}


def main():
    split = sys.argv[1]
    which = sys.argv[2:] or ["H1", "H2a", "H2b", "H3", "H4", "H5", "H6", "H7"]
    rng = TRAIN if split == "train" else HOLD
    ctx = Ctx()
    res = {}
    sides = None
    if split == "holdout" and (OUT / "train.json").exists():
        tr = json.load(open(OUT / "train.json"))
        sides = {k: v.get("side") for k, v in tr.items() if k.startswith("H7.")}
    for h in which:
        if h == "H1":
            for name, kw in {"H1": {}, "H1.flip": {"flip_exit": True},
                             "H1.optimistic": {"include_signal_settlement": True}}.items():
                ev = h1(ctx, rng, **kw)
                ex = [e for e in ev if e["exec"]]
                res[name] = {"periods": ev, "all_net0": summ(ev, "net0"), "exec_net0": summ(ex, "net0"),
                             "exec_net": summ(ex, "net"), "exec_net3": summ(ex, "net3"),
                             "n_all": len(ev), "n_exec": len(ex),
                             "exec_fund": summ(ex, "fund"), "exec_basis": summ(ex, "basis"),
                             "exec_borrow": summ(ex, "borrow")}
        elif h in ("H2a", "H2b"):
            p = h2(ctx, rng, 24 if h == "H2a" else 72)
            res[h] = {"periods": p, "net": summ(p), "gross": summ(p, "gross"), "fund": summ(p, "fund"), "cost": summ(p, "cost")}
        elif h == "H7":
            for k, (p, side) in h7(ctx, rng, sides).items():
                res[k] = {"periods": p, "net": summ(p), "gross": summ(p, "gross"), "side": side}
        else:
            p = {"H3": h3, "H4": h4, "H5": h5, "H6": h6}[h](ctx, rng)
            res[h] = {"periods": p, "net": summ(p), "gross": summ(p, "gross"), "fund": summ(p, "fund"), "cost": summ(p, "cost")}
        print(h, "done", flush=True)
    path = OUT / f"{split}{'_' + '_'.join(sys.argv[2:]) if sys.argv[2:] else ''}.json"
    json.dump(res, open(path, "w"), default=str)
    for k, v in res.items():
        print(k, {kk: vv for kk, vv in v.items() if kk != "periods"})


if __name__ == "__main__":
    main()

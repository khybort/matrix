"""Round 3b: H1 with a perp-perp hedge (see PREREG.txt).

usage:
  python3 research.py <data_dir> <round1_data_dir> episodes      # entry/exit/venue only, no returns; book_pairs.json
  python3 research.py <data_dir> <round1_data_dir> train          # all 6 cells, train split
  python3 research.py <data_dir> <round1_data_dir> holdout CELL.. # frozen cells, holdout split
  python3 research.py <data_dir> <round1_data_dir> record         # every cell, both splits, descriptive extras
Writes <data_dir>/results_<mode>.json and episodes_<split>.pkl.
"""
import json, math, pickle, sys
from pathlib import Path

import numpy as np
import pandas as pd

D, R1, MODE = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
HMS = 3600_000
T0 = 1759276800000  # 2025-10-01
TRAIN = (T0, 1780272000000)          # .. 2026-06-01
HOLD = (1780272000000, 1791676800000)  # .. 2026-10-10
NH = (HOLD[1] - T0) // HMS + 200
VENUES = ["binance", "okx", "bitget", "gate"]
FEE = {"bybit": 5.5, "binance": 5.0, "okx": 5.0, "bitget": 6.0, "gate": 5.0}
CELLS = {f"{x}.{e}": (thr, e) for x, thr in (("X08", -0.0008), ("X15", -0.0015)) for e in ("F24", "F48", "CONV")}
LEG_USD = 500.0
MMR = 0.01


def strip(b):
    for p in ("1000000", "100000", "10000", "1000", "1M"):
        if b.startswith(p) and len(b) > len(p) and not b[len(p):].isdigit():
            return b[len(p):]
    return b


def hidx(ms):
    return (np.asarray(ms, dtype=np.int64) - T0) // HMS


def series(df, key):
    """key -> (close, high, low) arrays on the global hour grid, indexed by bar START hour."""
    out = {}
    for k, g in df.groupby(key):
        i = hidx(g.ts.values)
        ok = (i >= 0) & (i < NH)
        c = np.full(NH, np.nan); h = c.copy(); lo = c.copy()
        c[i[ok]] = g.close.values[ok]; h[i[ok]] = g.high.values[ok]; lo[i[ok]] = g.low.values[ok]
        out[k] = (c, h, lo)
    return out


def fund_table(df, key):
    out = {}
    for k, g in df.groupby(key):
        t = (np.round(g.ts.values.astype(np.int64) / 60000) * 60000).astype(np.int64)
        o = np.argsort(t)
        t, r = t[o], g.rate.values.astype(float)[o]
        keep = np.r_[True, np.diff(t) > 0]
        t, r = t[keep], r[keep]
        gap = np.r_[8.0, np.clip(np.diff(t) / HMS, 1, 8)]
        out[k] = (t, r, r * 8 / gap)
    return out


def load():
    fb = pd.read_csv(R1 / "funding.csv")
    sig_syms = set(fb[fb.rate <= -0.0008].symbol)
    fb = fb[fb.symbol.isin(sig_syms)]
    kb = pd.read_csv(R1 / "kline_1h.csv", usecols=["symbol", "ts", "high", "low", "close"])
    kb = kb[kb.symbol.isin(sig_syms)]
    by_px, by_f = series(kb, "symbol"), fund_table(fb, "symbol")
    hv_px, hv_f = {}, {}
    for v in VENUES:
        k = pd.read_csv(D / f"{v}_kline.csv").drop_duplicates(["coin", "ts"])
        f = pd.read_csv(D / f"{v}_funding.csv")
        hv_px[v], hv_f[v] = series(k, "coin"), fund_table(f, "coin")
        vs = dict(zip(f.coin, f.vsym)); vs.update(dict(zip(k.coin, k.vsym)))
        hv_px[v]["_vsym"] = vs
    return by_f, by_px, hv_f, hv_px


def last_settled(ft, t_ms):
    t, r, n8 = ft
    j = np.searchsorted(t, t_ms, side="right") - 1
    return (t[j], r[j], n8[j]) if j >= 0 else None


def ceil_h(ms):
    return -(-ms // HMS) * HMS


def episodes(ctx, split, thr, exit_rule):
    by_f, by_px, hv_f, hv_px = ctx
    a, b = split
    out, skipped = [], {"no_bybit_px": 0, "no_venue": 0}
    for sym, (ts, rr, n8) in by_f.items():
        if sym not in by_px:
            continue
        coin = strip(sym[:-4])
        busy = -1
        for S, r, rn8 in zip(ts, rr, n8):
            if r > thr or S < a or S >= b or S < busy:
                continue
            E = ceil_h(S) + HMS
            ie = (E - T0) // HMS - 1  # bar ending at E
            c_by = by_px[sym][0]
            if ie < 0 or ie >= NH or not np.isfinite(c_by[ie]):
                skipped["no_bybit_px"] += 1
                continue
            best = None
            for v in VENUES:
                if coin not in hv_f[v] or coin not in hv_px[v]:
                    continue
                if not np.isfinite(hv_px[v][coin][0][ie]):
                    continue
                ls = last_settled(hv_f[v][coin], E)
                if ls is None or ls[0] < E - 9 * HMS:
                    continue
                if best is None or ls[2] > best[1]:
                    best = (v, ls[2])
            if best is None:
                skipped["no_venue"] += 1
                continue
            v = best[0]
            X = E + {"F24": 24, "F48": 48, "CONV": 96}[exit_rule] * HMS
            if exit_rule == "CONV":
                hf = hv_f[v][coin]
                for t2, n2 in zip(ts, n8):
                    if t2 <= E or t2 > E + 96 * HMS:
                        continue
                    ls = last_settled(hf, t2)
                    if ls is not None and n2 > ls[2]:
                        X = ceil_h(t2) + HMS
                        break
            busy = X
            out.append({"sym": sym, "coin": coin, "venue": v, "S": int(S), "rate0": float(r), "E": int(E), "X": int(X),
                        "hedge_n8_0": float(best[1]), "bybit_n8_0": float(rn8)})
    return out, skipped


# ---------------------------------------------------------------- order-book cost
def walk(levels, usd):
    """Volume-weighted fill price for `usd` notional against one side; None if the book is too thin."""
    rem, cost, qty = usd, 0.0, 0.0
    for p, q in levels:
        take = min(rem, p * q)
        cost += take; qty += take / p; rem -= take
        if rem <= 1e-9:
            return cost / qty
    return None


def book_cost(bk, usd):
    """Round-trip impact in bps (buy + sell against mid) on one book, or None."""
    if not bk:
        return None
    bids, asks = sorted(bk[0], key=lambda x: -x[0]), sorted(bk[1], key=lambda x: x[0])
    mid = (bids[0][0] + asks[0][0]) / 2
    pb, ps = walk(asks, usd), walk(bids, usd)
    if pb is None or ps is None:
        return None
    return (pb / mid - 1) * 1e4 + (1 - ps / mid) * 1e4


# ---------------------------------------------------------------- P&L
def price_at(c, i):
    """Close at bar index i, else the last close before it within the series (delisting mid-hold)."""
    if np.isfinite(c[i]):
        return c[i], False
    j = i
    while j >= 0 and not np.isfinite(c[j]):
        j -= 1
    return (c[j] if j >= 0 else np.nan), True


def pnl(ctx, books, e, gate_fee, usd=LEG_USD, fee_mult=1.0):
    by_f, by_px, hv_f, hv_px = ctx
    v, coin, sym = e["venue"], e["coin"], e["sym"]
    cb, hb, lb = by_px[sym]
    ch, hh, lh = hv_px[v][coin]
    ie = (e["E"] - T0) // HMS - 1
    ix = (e["X"] - T0) // HMS - 1
    pbe, phe = cb[ie], ch[ie]
    pbx, gap_b = price_at(cb, ix)
    phx, gap_h = price_at(ch, ix)
    vsym = hv_px[v]["_vsym"].get(coin)
    k_by, k_h = books.get(("bybit", sym)), books.get((v, vsym))
    cby, chd = book_cost(k_by, usd), book_cost(k_h, usd)
    if cby is None or chd is None:
        return None
    fee_h = max(FEE[v], gate_fee.get(vsym, 0)) if v == "gate" else FEE[v]
    fees = 2 * (FEE["bybit"] + fee_h) * fee_mult
    cost = fees + cby + chd

    # funding per settlement, each venue's own timestamps, MTM notional
    def fsum(ft, c, p0, a, b, sign):
        t, r, _ = ft
        m = (t > a) & (t <= b)
        tot = 0.0
        for tt, rr in zip(t[m], r[m]):
            px, _ = price_at(c, (ceil_h(tt) - T0) // HMS - 1)
            tot += sign * rr * (px / p0 if np.isfinite(px) else 1.0)
        return tot

    f_by = fsum(by_f[sym], cb, pbe, e["E"], e["X"], -1) * 1e4
    f_h = fsum(hv_f[v][coin], ch, phe, e["E"], e["X"], +1) * 1e4
    basis = ((pbx / pbe - 1) - (phx / phe - 1)) * 1e4
    net = f_by + f_h + basis - cost

    # hourly path inside the hold: bars ie+1 .. ix
    hrs = range(ie + 1, ix + 1)
    worst_close, worst_bound, worst_pos = 0.0, 0.0, 0.0
    liq = {3: None, 5: None}
    for i in hrs:
        c1, _ = price_at(cb, i); c2, _ = price_at(ch, i)
        bc = ((c1 / pbe - 1) - (c2 / phe - 1)) * 1e4
        lo = lb[i] if np.isfinite(lb[i]) else c1
        hi = hh[i] if np.isfinite(hh[i]) else c2
        bd = ((lo / pbe - 1) - (hi / phe - 1)) * 1e4
        tb = T0 + (i + 1) * HMS
        facc = (fsum(by_f[sym], cb, pbe, e["E"], tb, -1) + fsum(hv_f[v][coin], ch, phe, e["E"], tb, +1)) * 1e4
        worst_close, worst_bound, worst_pos = min(worst_close, bc), min(worst_bound, bd), min(worst_pos, bc + facc)
        for L in liq:
            if liq[L] is None:
                thr = 1 / L - MMR
                long_hit = lo / pbe - 1 <= -thr
                short_hit = hi / phe - 1 >= thr
                if long_hit or short_hit:
                    # liquidated leg loses its whole margin; survivor closed at this bar's close
                    leg_by = -1 / L if long_hit else (c1 / pbe - 1)
                    leg_h = -1 / L if short_hit else -(c2 / phe - 1)
                    surv_cost = 0.0
                    if not long_hit:
                        surv_cost += FEE["bybit"] * fee_mult + cby / 2
                    if not short_hit:
                        surv_cost += fee_h * fee_mult + chd / 2
                    entry_cost = (FEE["bybit"] + fee_h) * fee_mult + (cby + chd) / 2
                    liq[L] = {"hour": i - ie, "leg": "both" if long_hit and short_hit else ("bybit_long" if long_hit else "hedge_short"),
                              "net": (leg_by + leg_h) * 1e4 + facc - entry_cost - surv_cost}
    out = {"fund_by": f_by, "fund_h": f_h, "basis": basis, "cost": cost, "net": net,
           "worst_basis_close": worst_close, "worst_basis_bound": worst_bound, "worst_pos": worst_pos,
           "exit_gap": bool(gap_b or gap_h), "hours": (e["X"] - e["E"]) / HMS,
           "entry_ratio": pbe / phe}
    for L in liq:
        out[f"liq{L}"] = liq[L]["leg"] if liq[L] else None
        out[f"liq{L}_hour"] = liq[L]["hour"] if liq[L] else None
        out[f"net_L{L}"] = liq[L]["net"] if liq[L] else net
    return out


# ---------------------------------------------------------------- statistics
def betainc(a, b, x):
    """Regularized incomplete beta (Numerical Recipes continued fraction)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x > (a + 1) / (a + b + 2):
        return 1 - betainc(b, a, 1 - x)
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-30 else 1e-30); h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-30 else 1e-30
        de = d * c; h *= de
        if abs(de - 1) < 1e-12:
            break
    return math.exp(lbt) * h / a


def t_sf(t, df):
    """One-sided P(T >= t) for Student t."""
    x = df / (df + t * t)
    p = 0.5 * betainc(df / 2, 0.5, x)
    return p if t >= 0 else 1 - p


def clustered_t(x, groups):
    x = np.asarray(x, float); g = np.asarray(groups)
    n, m = len(x), x.mean()
    G = len(set(g))
    if n < 3 or G < 2:
        return float("nan"), G
    s = pd.Series(x - m).groupby(g).sum().values
    se = math.sqrt(G / (G - 1) * (s ** 2).sum()) / n
    return m / se, G


def summary(df, col="net"):
    x = df[col].values
    if len(x) < 3:
        return {"n": int(len(x))}
    wk = pd.to_datetime(df.S, unit="ms", utc=True).dt.strftime("%G-%V")
    dy = pd.to_datetime(df.S, unit="ms", utc=True).dt.strftime("%F")
    tw, G = clustered_t(x, wk)
    td, _ = clustered_t(x, dy)
    q = np.quantile(x, [0.05, 0.95])
    trim = x[(x >= q[0]) & (x <= q[1])].mean()
    srt = np.sort(x)
    top5 = srt[-max(1, int(round(0.05 * len(x)))):].sum()
    return {"n": int(len(x)), "mean": round(float(x.mean()), 1), "median": round(float(np.median(x)), 1),
            "trim5": round(float(trim), 1), "t_wk": round(tw, 2), "G_wk": G, "t_day": round(td, 2),
            "p_wk": t_sf(tw, G - 1) if np.isfinite(tw) else 1.0,
            "ex_top5pct_mean": round(float(srt[:-max(1, int(round(0.05 * len(x))))].mean()), 1),
            "top5pct_share": round(float(top5 / x.sum()), 2) if x.sum() > 0 else None}


def build(ctx, split, books, gate_fee, extras=False, only=None):
    res, tabs = {}, {}
    for cell, (thr, ex) in CELLS.items():
        if only is not None and cell not in only:
            continue
        eps, sk = episodes(ctx, split, thr, ex)
        rows, nobook = [], 0
        for e in eps:
            p = pnl(ctx, books, e, gate_fee)
            if p is None:
                nobook += 1
                continue
            r = {**e, **p}
            if extras:
                p5 = pnl(ctx, books, e, gate_fee, usd=5000.0)
                r["net_5k"] = p5["net"] if p5 else np.nan
                r["net_fee2"] = pnl(ctx, books, e, gate_fee, fee_mult=2.0)["net"]
            rows.append(r)
        df = pd.DataFrame(rows)
        tabs[cell] = df
        s = {"skipped": {**sk, "no_book": nobook}, "net": summary(df) if len(df) else {"n": 0}}
        if len(df):
            s["net_L3"] = summary(df, "net_L3"); s["net_L5"] = summary(df, "net_L5")
            s["parts_mean"] = {k: round(float(df[k].mean()), 1) for k in ("fund_by", "fund_h", "basis", "cost")}
            s["venue_mix"] = df.venue.value_counts().to_dict()
            s["liq3_share"] = round(float(df.liq3.notna().mean()), 3); s["liq5_share"] = round(float(df.liq5.notna().mean()), 3)
            s["liq5_by_leg"] = df.liq5.value_counts().to_dict(); s["liq3_by_leg"] = df.liq3.value_counts().to_dict()
            s["worst_basis_close_q"] = [round(float(v), 0) for v in df.worst_basis_close.quantile([0.5, 0.1, 0.01, 0.0])]
            s["worst_basis_bound_q"] = [round(float(v), 0) for v in df.worst_basis_bound.quantile([0.5, 0.1, 0.01, 0.0])]
            s["ret_on_capital_bps"] = {"unlev": round(float(df.net.mean()) / 2, 1),
                                       "L3": round(float(df.net_L3.mean()) * 1.5, 1), "L5": round(float(df.net_L5.mean()) * 2.5, 1)}
            s["binance_only_ref"] = summary(df[df.venue == "binance"]) if (df.venue == "binance").sum() > 2 else None
            if extras:
                s["net_5k"] = summary(df.dropna(subset=["net_5k"]), "net_5k")
                s["net_fee2"] = summary(df, "net_fee2")
                by_coin = df.groupby("sym").net.sum().sort_values(ascending=False)
                by_day = df.groupby(pd.to_datetime(df.S, unit="ms", utc=True).dt.date).net.sum().sort_values(ascending=False)
                tot = df.net.sum()
                s["top5_coin_share"] = round(float(by_coin.head(5).sum() / tot), 2) if tot > 0 else None
                s["top10_day_share"] = round(float(by_day.head(10).sum() / tot), 2) if tot > 0 else None
                s["by_month"] = df.groupby(pd.to_datetime(df.S, unit="ms", utc=True).dt.strftime("%Y-%m")).net.agg(["size", "mean"]).round(1).to_dict("index")
                s["entry_ratio_q"] = [round(float(v), 4) for v in (df.entry_ratio / df.entry_ratio.groupby(df.sym).transform("median")).quantile([0.05, 0.5, 0.95])]
        res[cell] = s
    return res, tabs


def main():
    ctx = load()
    gate_fee = {x["name"]: float(x["taker_fee_rate"]) * 1e4 for x in json.load(open(D / "gate_info.json"))}
    if MODE == "episodes":
        pairs = {"bybit": set()} | {v: set() for v in VENUES}
        for split in (TRAIN, HOLD):
            for cell, (thr, ex) in CELLS.items():
                eps, sk = episodes(ctx, split, thr, ex)
                print(cell, "train" if split == TRAIN else "holdout", len(eps), sk,
                      pd.Series([e["venue"] for e in eps]).value_counts().to_dict())
                for e in eps:
                    pairs["bybit"].add(e["sym"]); pairs[e["venue"]].add(ctx[3][e["venue"]]["_vsym"][e["coin"]])
        json.dump({k: sorted(v) for k, v in pairs.items()}, open(D / "book_pairs.json", "w"))
        return
    books = pickle.load(open(D / "books.pkl", "rb"))["books"]
    if MODE == "train":
        res, tabs = build(ctx, TRAIN, books, gate_fee)
    elif MODE == "holdout":
        keep = sys.argv[4:]
        res, tabs = build(ctx, HOLD, books, gate_fee, only=set(keep))
    else:
        res, tabs = {}, {}
        for nm, sp in (("train", TRAIN), ("holdout", HOLD)):
            r, t = build(ctx, sp, books, gate_fee, extras=True)
            res[nm] = r
            tabs.update({f"{nm}:{k}": v for k, v in t.items()})
        pd.to_pickle(tabs, D / "episodes_record.pkl")
    json.dump(res, open(D / f"results_{MODE}.json", "w"), indent=1, default=str)
    for k, v in (res.items() if MODE != "record" else [(f"{a}:{c}", s) for a, r in res.items() for c, s in r.items()]):
        print(k, json.dumps({kk: v.get(kk) for kk in ("net", "net_L3", "parts_mean", "venue_mix", "skipped")}, default=str))


if __name__ == "__main__":
    main()

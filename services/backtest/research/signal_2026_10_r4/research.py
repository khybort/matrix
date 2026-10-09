"""Round 4 (H13): long spot + short dated future, daily tranches, hold to delivery or exit early. Rules: PREREG.txt.
Usage: python3 research.py <data_dir> [--holdout]   (holdout cells are evaluated only for train passers unless --record)
Writes <data_dir>/tranches_<size>.pkl and prints the cell tables."""
import json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd

D = Path(sys.argv[1]); B = D / "binance"
FLAGS = set(sys.argv[2:])
ALTS = ["ADA", "BCH", "BNB", "DOT", "LINK", "LTC", "SOL", "XRP"]
TRAIN = (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-07-01", tz="UTC"))
HOLD = (pd.Timestamp("2024-07-01", tz="UTC"), pd.Timestamp("2026-10-09", tz="UTC"))
DATA_END = pd.Timestamp("2026-10-09 16:00", tz="UTC")
YS = [0.05, 0.08, 0.12]; Z = 0.02; LS = [1, 2, 3]; MM = 0.01
FEE_SPOT, FEE_FUT = 10e-4, 5e-4
SIZES = [500, 5000, 50000]; PRIMARY = 5000
M_PRIOR = 38 + 29 + 18 + 6


# ---------------------------------------------------------------- books
def walk(book, usd, side):
    """cost (fraction) vs mid of filling `usd` notional; side buy walks asks. NaN if depth is short."""
    bids, asks = book["bids"], book["asks"]
    mid = (bids[0][0] + asks[0][0]) / 2
    lv = asks if side == "buy" else bids
    left, qty_coin = usd, 0.0
    for p, q in lv:
        u = book["unit"]
        lvl_usd = {"coin": p * q, "usd": q, "usd_contracts": q * book["ct"], "coin_contracts": q * book["ct"] * p}[u]
        take = min(left, lvl_usd)
        qty_coin += take / p
        left -= take
        if left <= 1e-9:
            break
    if left > 1e-9:
        return float("nan")
    avg = usd / qty_coin
    return (avg / mid - 1) if side == "buy" else (1 - avg / mid)


def group_walks(books, size):
    """per group: spot buy, spot sell, spot TWAP slice sell (size/30), future sell, future buy — fractions of notional."""
    by = {(b["venue"], b["kind"], b["symbol"]): b for b in books}

    def spot(c):
        b = by[("binance", "spot", c + "USDT")]
        return walk(b, size, "buy"), walk(b, size, "sell"), walk(b, size / 30, "sell")

    def fut(venue, kinds, pred):
        bs = [b for (v, k, s), b in by.items() if v == venue and k in kinds and pred(s)]
        sells = [walk(b, size, "sell") for b in bs]; buys = [walk(b, size, "buy") for b in bs]
        return float(np.mean(sells)), float(np.mean(buys)), [b["symbol"] for b in bs]

    out = {}
    for c in ["BTC", "ETH"]:
        out[f"LIN_{c}"] = (spot(c), fut("binance", ("um",), lambda s, c=c: s.startswith(c + "USDT_")))
        out[f"INVB_{c}"] = (spot(c), fut("binance", ("cm",), lambda s, c=c: s.startswith(c + "USD_")))
        out[f"INVD_{c}"] = (spot(c), fut("deribit", ("month", "quarter"),  # Deribit tags quarterlies settlement_period=month
                                         lambda s, c=c: s.startswith(c + "-") and s[-5:-2] in ("MAR", "JUN", "SEP", "DEC")))
    for a in ALTS:
        out[f"INVB_ALT_{a}"] = (spot(a), fut("binance", ("cm",), lambda s, a=a: s.startswith(a + "USD_")))
    return out


# ---------------------------------------------------------------- data
def load_bars(path, mark_path=None):
    df = pd.read_pickle(path)
    df["tc"] = df.ts + pd.Timedelta(hours=1)  # bar close time
    df = df.set_index("tc")[["high", "close", "volume"]]
    if mark_path is not None and Path(mark_path).exists():
        mk = pd.read_pickle(mark_path); mk["tc"] = mk.ts + pd.Timedelta(hours=1)
        df["mark_high"] = mk.set_index("tc")["high"].reindex(df.index)
    else:
        df["mark_high"] = np.nan
    df["mark_high"] = df.mark_high.fillna(df.high)
    return df


def contracts():
    """[(group, coin, venue, structure, symbol, expiry, bars)]"""
    out = []
    bdel = pd.read_pickle(D / "binance_delivery.pkl")
    bdel["t"] = pd.to_datetime(bdel.deliveryTime, unit="ms", utc=True)
    ddel = pd.read_pickle(D / "deribit_delivery.pkl")
    for p in sorted(B.glob("*_klines_*_*.pkl")):
        name = p.stem
        if name.startswith("spot_"):
            continue
        mkt, _, sym = name.split("_", 2)
        coin = sym.split("USD")[0]
        exp = pd.Timestamp(f"20{sym[-6:-4]}-{sym[-4:-2]}-{sym[-2:]} 08:00", tz="UTC")
        pair = sym.split("_")[0]
        dp = bdel[(bdel.pair == pair) & (bdel.t.dt.date == exp.date())].deliveryPrice  # API stamps the date, 00:00
        group = (f"LIN_{coin}" if mkt == "um" else (f"INVB_{coin}" if coin in ("BTC", "ETH") else f"INVB_ALT"))
        out.append(dict(group=group, coin=coin, venue="binance", struct="LIN" if mkt == "um" else "INV", symbol=sym,
                        expiry=exp, delivery=float(dp.iloc[0]) if len(dp) else np.nan, window=60,
                        bars=load_bars(p, B / f"{mkt}_markPriceKlines_{sym}.pkl")))
    der = pd.read_pickle(D / "deribit_1h.pkl")
    der["tc"] = der.ts + pd.Timedelta(hours=1)
    for name, g in der.groupby("instrument"):
        coin = name.split("-")[0]
        exp = g.expiry.iat[0]
        dp = ddel[(ddel["index"] == coin.lower() + "_usd") & (ddel.date == exp.date().isoformat())].delivery_price
        bars = g.set_index("tc")[["high", "close", "volume"]].sort_index()
        bars["mark_high"] = bars.high
        out.append(dict(group=f"INVD_{coin}", coin=coin, venue="deribit", struct="INV", symbol=name, expiry=exp,
                        delivery=float(dp.iloc[0]) if len(dp) else np.nan, window=30, bars=bars))
    return out


def spot_bars():
    s = {}
    for c in ["BTC", "ETH"] + ALTS:
        df = pd.read_pickle(B / f"spot_klines_{c}USDT.pkl"); df["tc"] = df.ts + pd.Timedelta(hours=1)
        s[c] = df.set_index("tc")[["high", "close"]]
    return s


def settlement_twap(s1m, coin, expiry, window, spot_h, delivery=np.nan):
    """Spot sold as a TWAP over the venue's averaging window. Binance's window changed over the years (60 -> 30 min;
    the published delivery prices match one or the other): for Binance the window whose spot TWAP is closer to the
    published delivery price is taken as the rule in force at that expiry."""
    def tw(m):
        w = s1m[(s1m.coin == coin) & (s1m.ts >= expiry - pd.Timedelta(minutes=m)) & (s1m.ts < expiry)]
        return float(w.close.mean()) if len(w) >= m * 0.8 else np.nan
    cands = [tw(window)] if window == 30 else [tw(30), tw(60)]
    cands = [c for c in cands if not math.isnan(c)]
    if not cands:
        return float(spot_h.close.get(expiry, np.nan)), "1h"
    if math.isnan(delivery):
        return cands[-1], "1m"
    return min(cands, key=lambda c: abs(c - delivery)), "1m"


def rf_series():
    o = pd.read_pickle(D / "okx_usdt_lending.pkl").set_index("ts").lendingRate.sort_index()
    first = o[: o.index[0] + pd.Timedelta(days=30)].mean()
    idx = pd.date_range(pd.Timestamp("2020-06-01", tz="UTC"), DATA_END, freq="h")
    r = o.reindex(idx.union(o.index)).sort_index().ffill().reindex(idx).fillna(first)
    return r, o.index[0]


# ---------------------------------------------------------------- tranches
def build(size, walks, cons, spot, s1m, rf, walks_dec):
    rf_cum = rf.cumsum()

    def rf_mean(a, b):
        a, b = a.floor("h"), b.floor("h")
        if b <= a:
            return float(rf.get(a, rf.iloc[-1]))
        return float((rf_cum.get(b, rf_cum.iloc[-1]) - rf_cum.get(a, 0.0)) / ((b - a) / pd.Timedelta(hours=1)))

    rows = []
    by_gc = {}
    for c in cons:
        by_gc.setdefault((c["group"], c["coin"]), []).append(c)
    for (group, coin), cs in by_gc.items():
        wkey = group if group != "INVB_ALT" else f"INVB_ALT_{coin}"
        (sp_buy, sp_sell, sp_twap), (f_sell, f_buy, _) = walks[wkey]
        (d_buy, _, d_twap), (d_sell, _, _) = walks_dec[wkey]
        dec_cost = 2 * FEE_SPOT + 2 * FEE_FUT + d_buy + d_sell + d_twap   # entry rule always uses the primary size
        sp = spot[coin]
        spc = sp.close.to_dict()
        for c in cs:
            c.setdefault("cl", c["bars"].close.to_dict()); c.setdefault("vol", c["bars"].volume.to_dict())
        struct = cs[0]["struct"]
        cost_entry = FEE_SPOT + FEE_FUT + sp_buy + f_sell
        cost_hold_exit = FEE_SPOT + FEE_FUT + sp_twap          # delivery charged as taker; TWAP slices walked
        cost_early_exit = FEE_SPOT + FEE_FUT + sp_sell + f_buy
        days = pd.date_range(max(TRAIN[0], min(c["bars"].index.min() for c in cs).ceil("D")), DATA_END.floor("D"),
                             freq="D")
        for day in days:
            t_dec, t_ex = day + pd.Timedelta(hours=8), day + pd.Timedelta(hours=9)
            if t_ex > DATA_END or t_dec not in spc or t_ex not in spc:
                continue
            best = None
            for c in cs:
                dte = (c["expiry"] - t_dec) / pd.Timedelta(days=1)
                if not 14 <= dte <= 200:
                    continue
                cl = c["cl"]
                if t_dec not in cl or t_ex not in cl or c["vol"].get(t_ex, 0) <= 0:
                    continue
                b_gross = cl[t_dec] / spc[t_dec] - 1
                b_net = (b_gross - dec_cost) * 365 / dte
                if best is None or b_net > best[0]:
                    best = (b_net, c, dte, b_gross)
            if best is None:
                continue
            b_net, c, dte, b_gross = best
            bars, cl = c["bars"], c["cl"]
            F0, S0 = cl[t_ex], spc[t_ex]
            # EARLY exit search: daily 08:00 checks after entry
            early = None
            for d2 in pd.date_range(t_dec + pd.Timedelta(days=1), c["expiry"], freq="D"):
                if d2 >= c["expiry"] - pd.Timedelta(days=1) or d2 + pd.Timedelta(hours=1) > DATA_END:
                    break
                if d2 in cl and d2 in spc:
                    rem = (cl[d2] / spc[d2] - 1) * 365 / ((c["expiry"] - d2) / pd.Timedelta(days=1))
                    te = d2 + pd.Timedelta(hours=1)
                    if rem <= Z and te in cl and te in spc:
                        early = te
                        break
            delivered = c["expiry"] <= DATA_END
            if delivered:
                twap, twap_src = settlement_twap(s1m, coin, c["expiry"], c["window"], sp, c["delivery"])
                twap_src += "" if not math.isnan(c["delivery"]) else "_nodelivery"
                # no published price (Binance API keeps ~12-18 deliveries): settle at the 30-min spot TWAP, the measured
                # tracking where prices exist is 1 +- 6 bps; the futures' last 1h close is a far worse proxy (+-80 bps)
                Dp = c["delivery"] if not math.isnan(c["delivery"]) else settlement_twap(s1m, coin, c["expiry"], 30, sp)[0]
            for exit_rule in ("HOLD", "EARLY"):
                if exit_rule == "EARLY" and early is not None:
                    t_out, F1, S1, cx, how = early, cl[early], spc[early], cost_early_exit, "early"
                elif delivered:
                    t_out, F1, S1, cx, how = c["expiry"], Dp, twap, cost_hold_exit, "delivery"
                else:
                    rows.append(dict(group=group, coin=coin, symbol=c["symbol"], exit=exit_rule, entry=t_ex,
                                     excluded="delivers_after_data_end", b_net=b_net))
                    continue
                hold_days = (t_out - t_ex) / pd.Timedelta(days=1)
                path = bars.loc[(bars.index > t_ex) & (bars.index <= min(t_out, bars.index.max()))]
                mh = path.mark_high.max() if len(path) else F0
                adverse = max(0.0, mh / F0 - 1)
                if struct == "INV":
                    gross = (S1 / F1) * (F0 / S0) - 1
                else:
                    gross = (S1 - S0 + F0 - F1) / S0
                fut_n = F0 / S0  # futures notional per unit of spot notional (equal-quantity hedge)
                net = gross - cost_entry - cx
                base = dict(group=group, coin=coin, symbol=c["symbol"], venue=c["venue"], struct=struct,
                            exit=exit_rule, how=how, entry=t_ex, out=t_out, hold_days=hold_days, dte=dte,
                            b_gross_dec=b_gross, b_net=b_net, basis_entry=F0 / S0 - 1, gross=gross, net=net,
                            track=(S1 / F1 - 1) if how == "delivery" else np.nan,
                            twap_src=twap_src if how == "delivery" else "", adverse=adverse,
                            rf=rf_mean(t_ex, t_out), excluded="", size=size)
                if struct == "INV":
                    rows.append({**base, "L": 0, "capital": 1.0, "pnl": net, "liq": False, "topup": 0.0})
                    continue
                for L in LS:
                    thr = F0 * (1 + 1 / L - MM)
                    hit = path.index[path.mark_high >= thr]
                    topup = 0.0
                    if len(path):
                        m = path.mark_high.values
                        topup = float(max(0.0, np.max(fut_n * (m / F0 - 1) + MM * fut_n * m / F0 - fut_n / L)))
                    cap = 1 + fut_n / L
                    if len(hit):
                        h = hit[0]
                        Sh = spc.get(h, np.nan)
                        pnl = (Sh / S0 - 1) - fut_n / L - cost_entry - (FEE_SPOT + sp_sell)
                        hd = max((h - t_ex) / pd.Timedelta(days=1), 1 / 24)
                        rows.append({**base, "L": L, "capital": cap, "pnl": pnl, "liq": True, "topup": topup,
                                     "out": h, "hold_days": hd, "how": "liquidated", "rf": rf_mean(t_ex, h)})
                    else:
                        rows.append({**base, "L": L, "capital": cap, "pnl": net, "liq": False, "topup": topup})
    df = pd.DataFrame(rows)
    ok = df.excluded == ""
    df.loc[ok, "ret"] = df.loc[ok, "pnl"] / df.loc[ok, "capital"]
    df.loc[ok, "ann"] = df.loc[ok, "ret"] * 365 / df.loc[ok, "hold_days"]
    df.loc[ok, "e"] = df.loc[ok, "ann"] - df.loc[ok, "rf"]
    df.loc[ok, "e5"] = df.loc[ok, "ann"] - 0.05
    df["week"] = df.entry.dt.strftime("%G-%V")
    return df


# ---------------------------------------------------------------- stats
def _betacf(a, b, x):
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
    return h


def _ibeta(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1 - bt * _betacf(b, a, 1 - x) / b


def t_sf(t, df):
    """one-sided P(T > t), Student t."""
    x = df / (df + t * t)
    tail = 0.5 * _ibeta(df / 2, 0.5, x)
    return tail if t > 0 else 1 - tail


def ct(x, g):
    x = np.asarray(x, float); g = np.asarray(g)
    n = len(x)
    if n < 3:
        return np.nan, np.nan, 0
    u = x - x.mean()
    s = pd.Series(u).groupby(g).sum().values
    G = len(s)
    if G < 2:
        return np.nan, np.nan, G
    se = math.sqrt((s ** 2).sum() * G / (G - 1)) / n
    t = x.mean() / se if se > 0 else np.nan
    p = t_sf(t, G - 1) if not np.isnan(t) else np.nan
    return t, p, G


def cells(df, Ys=YS):
    out = []
    for group in ["LIN_BTC", "LIN_ETH", "INVB_BTC", "INVB_ETH", "INVB_ALT", "INVD_BTC", "INVD_ETH"]:
        g0 = df[(df.group == group) & (df.excluded == "")]
        Ls = LS if group.startswith("LIN") else [0]
        for Y in Ys:
            for ex in ("HOLD", "EARLY"):
                for L in Ls:
                    c = g0[(g0.b_net >= Y) & (g0.exit == ex) & (g0.L == L)]
                    for split, (a, b) in (("train", TRAIN), ("holdout", HOLD)):
                        s = c[(c.entry >= a) & (c.entry < b)]
                        t, p, G = ct(s.e, s.week)
                        tc, _, Gc = ct(s.e, s.symbol)
                        mw = (s.pnl.sum() / (s.capital * s.hold_days).sum() * 365 -
                              (s.rf * s.capital * s.hold_days).sum() / (s.capital * s.hold_days).sum()) if len(s) else np.nan
                        out.append(dict(cell=f"{group}.Y{int(Y*100)}.{ex}" + (f".L{L}" if L else ""), group=group, Y=Y,
                                        exit=ex, L=L, split=split, n=len(s), weeks=G, mean_e=s.e.mean(),
                                        median_e=s.e.median(), t_wk=t, p=p, t_contract=tc, contracts=Gc, mw_excess=mw,
                                        mean_ann=s.ann.mean(), mean_rf=s.rf.mean(), mean_e5=s.e5.mean(),
                                        liq=s.liq.mean() if len(s) else np.nan, mean_hold=s.hold_days.mean()))
    return pd.DataFrame(out)


if __name__ == "__main__":
    books = json.loads((D / "books_today.json").read_text())["books"]
    cons = contracts(); spot = spot_bars(); s1m = pd.read_pickle(D / "spot_1m_expiry_days.pkl"); rf, rf0 = rf_series()
    print("contracts", len(cons), "rf series from", rf0)
    walks_dec = group_walks(books, PRIMARY)
    for size in SIZES:
        walks = group_walks(books, size)
        df = build(size, walks, cons, spot, s1m, rf, walks_dec)
        df.to_pickle(D / f"tranches_{size}.pkl")
        print("size", size, "tranches", len(df), "excluded", (df.excluded != "").sum())

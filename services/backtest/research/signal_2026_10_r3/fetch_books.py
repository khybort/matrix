"""Round 3: today's full public books (Bybit linear perp + spot leg on Bybit and/or Binance) for every
perp with a settlement >= 0.05 %, walked at $500 and $5k per leg (shb method: fills against mid, plus
31 bps of taker fees). usage: python3 fetch_books.py <round1 data dir> <r3 work dir> [extra perp symbols file]"""
import json, pickle, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd

D, W = Path(sys.argv[1]), Path(sys.argv[2])
FEES = 31.0


def coin(sym):
    b = sym[:-4]
    for p in ("1000000", "100000", "10000", "1000"):
        if b.startswith(p) and len(b) > len(p):
            return b[len(p):]
    return b


def get(u):
    for a in range(4):
        try:
            return json.loads(urllib.request.urlopen(u, timeout=15).read())
        except Exception:
            time.sleep(1 + a)


def lv(raw):
    return [(float(p), float(q)) for p, q, *_ in raw or [] if float(q) > 0]


def walk(levels, mid, usd):
    rem, c = usd, 0.0
    for p, q in levels:
        t = min(rem, p * q); c += t * abs(p - mid) / mid; rem -= t
        if rem <= 1e-9:
            return c / usd * 1e4
    return np.nan


def perp_book(sym):
    j = get(f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={sym}&limit=500")
    time.sleep(0.1)
    if j and j.get("result") and j["result"].get("b") and j["result"].get("a"):
        return lv(j["result"]["b"]), lv(j["result"]["a"])


def spot_book(c, v):
    if v == "bybit":
        j = get(f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={c}USDT&limit=200")
        time.sleep(0.1)
        if j and j.get("result") and j["result"].get("b") and j["result"].get("a"):
            return lv(j["result"]["b"]), lv(j["result"]["a"])
    else:
        j = get(f"https://api.binance.com/api/v3/depth?symbol={c}USDT&limit=1000")
        time.sleep(0.1)
        if j and j.get("bids") and j.get("asks"):
            return lv(j["bids"]), lv(j["asks"])


f = pd.read_csv(D / "funding.csv")
syms = sorted(set(f[f.rate >= 0.0005].symbol.unique()) | (set(open(sys.argv[3]).read().split()) if len(sys.argv) > 3 else set()))
sp = pd.read_pickle(W / "spot_1h.pkl")
venues = sp.groupby("coin").venue.unique().to_dict()
with ThreadPoolExecutor(6) as ex:
    perps = dict(zip(syms, ex.map(perp_book, syms)))
pairs = sorted({(c, v) for c, vs in venues.items() for v in vs})
with ThreadPoolExecutor(6) as ex:
    spots = dict(zip(pairs, ex.map(lambda cv: spot_book(*cv), pairs)))
rows = {}
for s in syms:
    c = coin(s)
    for v in venues.get(c, []):
        P, S = perps.get(s), spots.get((c, v))
        if not (P and S and P[0] and P[1] and S[0] and S[1]):
            continue
        mp, ms = (P[0][0][0] + P[1][0][0]) / 2, (S[0][0][0] + S[1][0][0]) / 2
        r = {}
        for usd, k in ((500, "cost500"), (5000, "cost5k")):
            r[k] = FEES + walk(P[0], mp, usd) + walk(P[1], mp, usd) + walk(S[1], ms, usd) + walk(S[0], ms, usd)
        r["perp_spread"] = (P[1][0][0] - P[0][0][0]) / mp * 1e4
        r["spot_spread"] = (S[1][0][0] - S[0][0][0]) / ms * 1e4
        rows[(s, v)] = r
pickle.dump({"fetched_utc": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "rows": rows}, open(W / "books.pkl", "wb"))
d = pd.DataFrame(rows).T
print(len(syms), "perps,", sum(1 for v in perps.values() if v), "perp books;", len(pairs), "spot pairs,",
      sum(1 for v in spots.values() if v), "spot books;", len(rows), "walkable pairs")
print(d.describe(percentiles=[.1, .5, .9]).round(1).to_string())

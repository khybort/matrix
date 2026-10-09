"""Round 3: 1h spot klines, 2025-09-30 .. 2026-10-10, for every perp coin with a settlement >= 0.05 %.
Bybit spot <COIN>USDT and Binance spot <COIN>USDT for every coin (research.py prefers Bybit at entry).
Bybit returns its newest 1000 bars at or before `end` even when they predate `start` (a pair delisted
before the window), so bars before T0 are dropped and Binance is always fetched.
usage: python3 fetch_spot.py <round1 data dir> <out dir> [extra perp symbols file]   (public endpoints only)
Resumable: coins already in <out>/spot_1h.pkl are skipped."""
import json, sys, time, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd

D, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
T0, T1 = 1759190400000, 1791590400000  # 2025-09-30, 2026-10-10 UTC
HMS = 3600_000
rl = threading.Semaphore(3)


def coin(sym):
    b = sym[:-4]
    for p in ("1000000", "100000", "10000", "1000"):
        if b.startswith(p) and len(b) > len(p):
            return b[len(p):]
    return b


def get(url):
    for a in range(6):
        try:
            with rl:
                with urllib.request.urlopen(url, timeout=20) as r:
                    j = json.loads(r.read())
                time.sleep(0.12)
            return j
        except Exception as ex:
            if getattr(ex, "code", None) == 400:
                return None
            time.sleep(1 + 2 * a)
    return None


def bybit(c):
    rows, end = [], T1
    while end > T0:
        j = get(f"https://api.bybit.com/v5/market/kline?category=spot&symbol={c}USDT&interval=60&start={T0}&end={end}&limit=1000")
        if not j or j.get("retCode") != 0 or not j["result"]["list"]:
            break
        L = j["result"]["list"]
        rows += [(int(x[0]), float(x[2]), float(x[3]), float(x[4]), float(x[6])) for x in L if int(x[0]) >= T0]
        oldest = min(int(x[0]) for x in L)
        if len(L) < 1000:
            break
        end = oldest - 1
    return rows


def binance(c):
    rows, start = [], T0
    while start < T1:
        j = get(f"https://api.binance.com/api/v3/klines?symbol={c}USDT&interval=1h&startTime={start}&endTime={T1}&limit=1000")
        if not isinstance(j, list) or not j:
            break
        rows += [(int(x[0]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in j]
        if len(j) < 1000:
            break
        start = int(j[-1][0]) + HMS
    return rows


def job(c, venues=("bybit", "binance")):
    out = []
    if "bybit" in venues:
        out += [(c, "bybit", *r) for r in bybit(c)]
    if "binance" in venues:
        out += [(c, "binance", *r) for r in binance(c)]
    return out


f = pd.read_csv(D / "funding.csv")
coins = sorted({coin(s) for s in f[f.rate >= 0.0005].symbol.unique()} |
               ({coin(s) for s in open(sys.argv[3]).read().split()} if len(sys.argv) > 3 else set()))
old = pd.read_pickle(OUT / "spot_1h.pkl") if (OUT / "spot_1h.pkl").exists() else None
todo = [(c, ("bybit", "binance")) for c in coins]
if old is not None:  # resume: refetch only the venues a coin has no rows for
    old = old[old.ts >= T0]
    have = set(zip(old.coin, old.venue))
    todo = [(c, tuple(v for v in vs if (c, v) not in have)) for c, vs in todo]
    todo = [t for t in todo if t[1]]
print(len(todo), "coins to fetch", flush=True)
rows = [] if old is None else list(old.itertuples(index=False, name=None))
with ThreadPoolExecutor(3) as ex:
    for i, r in enumerate(ex.map(lambda t: job(*t), todo)):
        rows += r
        if i % 50 == 0:
            print(i, len(rows), flush=True)
df = pd.DataFrame(rows, columns=["coin", "venue", "ts", "high", "low", "close", "qturn"]).drop_duplicates(["coin", "venue", "ts"])
df.to_pickle(OUT / "spot_1h.pkl")
print("done", len(df), df.groupby("venue").coin.nunique().to_dict())

"""Round 3b: hedge-venue public history for every coin with a Bybit settlement <= -0.08 % in the year.

usage: python3 fetch_hedge.py <data_dir> <round1_data_dir>
Needs in <data_dir>: bn_info.json (fapi exchangeInfo), okx_info.json (public/instruments SWAP),
bg_info.json (v2 mix contracts usdt-futures), gate_info.json (futures/usdt/contracts).
Writes <data_dir>/<venue>_funding.csv (coin, venue_symbol, ts_ms, rate) and <venue>_kline.csv
(coin, venue_symbol, open_ts_ms, open, high, low, close). Public endpoints only.
"""
import csv, json, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

D, R1 = sys.argv[1], sys.argv[2]
START = 1759276800000  # 2025-10-01
NOW = int(time.time() * 1000)
HOUR = 3600_000


def strip(b):
    for p in ("1000000", "100000", "10000", "1000", "1M"):
        if b.startswith(p) and len(b) > len(p) and not b[len(p):].isdigit():
            return b[len(p):]
    return b


def get(url, sem, pause):
    for a in range(8):
        try:
            with sem:
                with urllib.request.urlopen(url, timeout=30) as r:
                    j = json.loads(r.read())
                time.sleep(pause)
            return j
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(2 + 3 * a)
        except Exception:
            time.sleep(2 + 3 * a)
    return None


def coins():
    f = pd.read_csv(f"{R1}/funding.csv")
    syms = f[f.rate <= -0.0008].symbol.unique()
    return sorted({strip(s[:-4]) for s in syms})


def venue_maps():
    m = {}
    bn = json.load(open(f"{D}/bn_info.json"))["symbols"]
    m["binance"] = {strip(x["baseAsset"]): x["symbol"] for x in bn
                    if x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"}
    ok = json.load(open(f"{D}/okx_info.json"))["data"]
    m["okx"] = {strip(x["uly"].split("-")[0]): x["instId"] for x in ok if x["instId"].endswith("-USDT-SWAP")}
    bg = json.load(open(f"{D}/bg_info.json"))["data"]
    m["bitget"] = {strip(x["baseCoin"]): x["symbol"] for x in bg if x["quoteCoin"] == "USDT"}
    g = json.load(open(f"{D}/gate_info.json"))
    m["gate"] = {strip(x["name"][:-5]): x["name"] for x in g if x["name"].endswith("_USDT")}
    return m


SEM = {"binance": threading.Semaphore(3), "okx": threading.Semaphore(3),
       "bitget": threading.Semaphore(4), "gate": threading.Semaphore(4)}
BN_FUND = threading.Semaphore(1)
PAUSE = {"binance": 0.35, "okx": 0.35, "bitget": 0.2, "gate": 0.25}


def binance(coin, sym):
    fu, kl = [], []
    s = START
    while True:
        j = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={s}&limit=1000", BN_FUND, 0.7)
        if not j:
            break
        fu += [(coin, sym, int(x["fundingTime"]), x["fundingRate"]) for x in j]
        if len(j) < 1000:
            break
        s = int(j[-1]["fundingTime"]) + 1
    s = START
    while s < NOW:
        j = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&startTime={s}&limit=1500", SEM["binance"], PAUSE["binance"])
        if not j:
            break
        kl += [(coin, sym, int(x[0]), x[1], x[2], x[3], x[4]) for x in j]
        if len(j) < 1500:
            break
        s = int(j[-1][0]) + HOUR
    return fu, kl


def okx(coin, sym):
    fu, kl = [], []
    after = ""
    while True:
        j = get(f"https://www.okx.com/api/v5/public/funding-rate-history?instId={sym}&limit=400{after}", SEM["okx"], PAUSE["okx"])
        d = (j or {}).get("data") or []
        fu += [(coin, sym, int(x["fundingTime"]), x.get("realizedRate") or x["fundingRate"]) for x in d]
        if len(d) < 400:
            break
        after = f"&after={min(int(x['fundingTime']) for x in d)}"
    lo = min((r[2] for r in fu), default=NOW) - 48 * HOUR
    after = NOW
    while after > lo:
        j = get(f"https://www.okx.com/api/v5/market/history-candles?instId={sym}&bar=1H&after={after}&limit=100", SEM["okx"], PAUSE["okx"])
        d = (j or {}).get("data") or []
        if not d:
            break
        kl += [(coin, sym, int(x[0]), x[1], x[2], x[3], x[4]) for x in d]
        after = min(int(x[0]) for x in d)
    return fu, kl


def bitget(coin, sym):
    fu, kl = [], []
    for p in range(1, 20):
        j = get(f"https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol={sym}&productType=usdt-futures&pageSize=100&pageNo={p}", SEM["bitget"], PAUSE["bitget"])
        d = (j or {}).get("data") or []
        fu += [(coin, sym, int(x["fundingTime"]), x["fundingRate"]) for x in d]
        if len(d) < 100:
            break
    lo = min((r[2] for r in fu), default=NOW) - 48 * HOUR
    end = NOW
    while end > lo:
        j = get(f"https://api.bitget.com/api/v2/mix/market/history-candles?symbol={sym}&productType=usdt-futures&granularity=1H&endTime={end}&limit=200", SEM["bitget"], PAUSE["bitget"])
        d = (j or {}).get("data") or []
        if not d:
            break
        kl += [(coin, sym, int(x[0]), x[1], x[2], x[3], x[4]) for x in d]
        end = min(int(x[0]) for x in d) - 1
    return fu, kl


def gate(coin, sym):
    fu, kl = [], []
    lo = (NOW // 1000) - 179 * 86400
    a = lo
    while a < NOW // 1000:
        b = a + 20 * 86400
        j = get(f"https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract={sym}&limit=1000&from={a}&to={b}", SEM["gate"], PAUSE["gate"])
        if isinstance(j, list):
            fu += [(coin, sym, int(x["t"]) * 1000, x["r"]) for x in j]
        a = b
    if not fu:
        return fu, kl
    a = min(r[2] for r in fu) // 1000 - 48 * 3600
    while a < NOW // 1000:
        b = a + 1999 * 3600
        j = get(f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={sym}&interval=1h&from={a}&to={b}", SEM["gate"], PAUSE["gate"])
        if isinstance(j, list):
            kl += [(coin, sym, int(x["t"]) * 1000, x["o"], x["h"], x["l"], x["c"]) for x in j]
        a = b + 3600
    return fu, kl


def main():
    cs = coins()
    maps = venue_maps()
    fns = {"binance": binance, "okx": okx, "bitget": bitget, "gate": gate}

    def run_venue(v):
        jobs = [(c, maps[v][c]) for c in cs if c in maps[v]]
        print(v, "coins", len(jobs), flush=True)
        ff = open(f"{D}/{v}_funding.csv", "w", newline=""); fk = open(f"{D}/{v}_kline.csv", "w", newline="")
        wf, wk = csv.writer(ff), csv.writer(fk)
        wf.writerow(["coin", "vsym", "ts", "rate"]); wk.writerow(["coin", "vsym", "ts", "open", "high", "low", "close"])
        lk = threading.Lock(); n = [0]

        def one(job):
            fu, kl = fns[v](*job)
            with lk:
                wf.writerows(fu); wk.writerows(kl); n[0] += 1
                if n[0] % 50 == 0:
                    print(v, n[0], "/", len(jobs), flush=True)

        with ThreadPoolExecutor(4) as ex:
            list(ex.map(one, jobs))
        ff.close(); fk.close()
        print(v, "done", flush=True)

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(run_venue, fns))


if __name__ == "__main__":
    main()

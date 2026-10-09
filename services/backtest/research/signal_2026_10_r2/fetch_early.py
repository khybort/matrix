"""Bybit public 1h klines, 1h OI and funding for 2024-10-01..2025-09-30 (H11c confirmation).
  python3 -I fetch_early.py <r1_data_dir> <out_dir>   (symbols = those in r1 oi_1h.csv)"""
import csv, json, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

B = "https://api.bybit.com/v5/market/"
START, END = 1727740800000, 1759276800000 - 1  # 2024-10-01 .. 2025-09-30 23:59
d1, out = sys.argv[1], sys.argv[2]
syms = sorted(pd.read_csv(f"{d1}/oi_1h.csv", usecols=["symbol"]).symbol.unique())
lock = threading.Lock()


def get(path, q):
    for a in range(8):
        try:
            with urllib.request.urlopen(B + path + "?" + q, timeout=30) as r:
                j = json.loads(r.read())
            time.sleep(0.15)
            if j.get("retCode") == 0:
                return j["result"]
        except Exception:
            pass
        time.sleep(2 + 3 * a)
    raise RuntimeError(q)


def pages(path, q, key, ts_of, limit):
    rows, end = [], END
    while True:
        lst = get(path, q + f"&endTime={end}&end={end}&limit={limit}")["list"]
        if not lst:
            break
        rows += lst
        oldest = min(ts_of(x) for x in lst)
        if len(lst) < limit or oldest <= START:
            break
        end = oldest - 1
    return rows


def job(sym):
    k = pages("kline", f"category=linear&symbol={sym}&interval=60&start={START}&startTime={START}", "list", lambda x: int(x[0]), 1000)
    o, cur = [], ""
    while True:
        res = get("open-interest", f"category=linear&symbol={sym}&intervalTime=1h&startTime={START}&endTime={END}&limit=200" + (f"&cursor={cur}" if cur else ""))
        o += res["list"]
        cur = res.get("nextPageCursor") or ""
        if not res["list"] or not cur:
            break
    f = pages("funding/history", f"category=linear&symbol={sym}&startTime={START}", "list", lambda x: int(x["fundingRateTimestamp"]), 200)
    return sym, k, o, f


fk, fo, ff = (open(f"{out}/{n}.csv", "w", newline="") for n in ("kline_1h", "oi_1h", "funding"))
wk, wo, wf = csv.writer(fk), csv.writer(fo), csv.writer(ff)
wk.writerow(["symbol", "ts", "open", "high", "low", "close", "volume", "turnover"])
wo.writerow(["symbol", "ts", "oi"])
wf.writerow(["symbol", "ts", "rate"])
with ThreadPoolExecutor(4) as ex:
    for i, (sym, k, o, f) in enumerate(ex.map(job, syms)):
        wk.writerows([sym] + x for x in k if START <= int(x[0]) <= END)
        wo.writerows((sym, x["timestamp"], x["openInterest"]) for x in o)
        wf.writerows((sym, x["fundingRateTimestamp"], x["fundingRate"]) for x in f if START <= int(x["fundingRateTimestamp"]) <= END)
        if i % 20 == 0:
            print(i, sym, len(k), len(o), len(f), flush=True)
for fh in (fk, fo, ff):
    fh.close()
print("done", flush=True)

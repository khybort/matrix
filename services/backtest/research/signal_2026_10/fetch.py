"""Fetch one year of Bybit public history for every USDT linear perp:
funding settlements, 1h perp klines (price + turnover), 1h premium-index klines.
Output: data/funding.csv, data/kline_1h.csv, data/premium_1h.csv (appended per symbol).
"""
import csv, json, sys, time, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://api.bybit.com/v5/market/"
START = 1759276800000  # 2025-10-01 00:00 UTC
END = int(time.time() * 1000)
D = sys.argv[1] if len(sys.argv) > 1 else "data"

lock = threading.Lock()
rl = threading.Semaphore(8)


def get(path, params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    for attempt in range(8):
        try:
            with rl:
                with urllib.request.urlopen(BASE + path + "?" + q, timeout=30) as r:
                    j = json.loads(r.read())
                time.sleep(0.25)
            if j.get("retCode") == 0:
                return j["result"]
            time.sleep(2 + attempt * 3)
        except Exception:
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"failed {path} {params}")


def funding(sym):
    out, end = [], END
    while True:
        res = get("funding/history", {"category": "linear", "symbol": sym, "startTime": START, "endTime": end, "limit": 200})
        lst = res["list"]
        if not lst:
            break
        out += [(sym, int(x["fundingRateTimestamp"]), x["fundingRate"]) for x in lst]
        oldest = min(int(x["fundingRateTimestamp"]) for x in lst)
        if len(lst) < 200 or oldest <= START:
            break
        end = oldest - 1
    return out


def klines(sym, path):
    out, end = [], END
    while True:
        res = get(path, {"category": "linear", "symbol": sym, "interval": "60", "start": START, "end": end, "limit": 1000})
        lst = res["list"]
        if not lst:
            break
        out += [[sym] + x for x in lst]
        oldest = min(int(x[0]) for x in lst)
        if len(lst) < 1000 or oldest <= START:
            break
        end = oldest - 1
    return out


def main():
    ins = json.load(open(f"{D}/linear_instr.json"))["result"]["list"]
    syms = sorted(x["symbol"] for x in ins if x["quoteCoin"] == "USDT" and x["contractType"] == "LinearPerpetual" and x["status"] == "Trading")
    files = {k: open(f"{D}/{k}.csv", "w", newline="") for k in ("funding", "kline_1h", "premium_1h")}
    w = {k: csv.writer(f) for k, f in files.items()}
    w["funding"].writerow(["symbol", "ts", "rate"])
    w["kline_1h"].writerow(["symbol", "ts", "open", "high", "low", "close", "volume", "turnover"])
    w["premium_1h"].writerow(["symbol", "ts", "open", "high", "low", "close"])
    done = [0]

    def job(sym):
        f = funding(sym)
        k = klines(sym, "kline")
        p = klines(sym, "premium-index-price-kline")
        with lock:
            w["funding"].writerows(f)
            w["kline_1h"].writerows(k)
            w["premium_1h"].writerows(p)
            done[0] += 1
            if done[0] % 25 == 0:
                print(f"{done[0]}/{len(syms)}", flush=True)
                for fh in files.values():
                    fh.flush()

    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(job, syms):
            pass
    for fh in files.values():
        fh.close()
    print("done", flush=True)


main()

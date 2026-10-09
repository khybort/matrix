"""Bybit 5m linear klines (public REST) covering [T-65m, T+250m] of every H9 event, merged per symbol.
  python3 -I fetch_5m.py <h9_events.csv> <out.csv>"""
import csv, json, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

M = 60_000
e = pd.read_csv(sys.argv[1])
ranges = []
for s, g in e.groupby("symbol"):
    cur = None
    for t in sorted(g.ts):
        a, b = t - 65 * M, t + 250 * M
        if cur and a <= cur[1] + 5 * M:
            cur[1] = max(cur[1], b)
        else:
            if cur:
                ranges.append((s, *cur))
            cur = [a, b]
    ranges.append((s, *cur))
chunks = []
for s, a, b in ranges:
    while a <= b:
        chunks.append((s, a, min(b, a + 995 * 5 * M)))
        a += 1000 * 5 * M
print("requests", len(chunks), flush=True)
lock, rl = threading.Lock(), threading.Semaphore(8)
out = open(sys.argv[2], "w", newline="")
w = csv.writer(out)
w.writerow(["symbol", "ts", "open", "close"])
done = [0]


def job(c):
    s, a, b = c
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={s}&interval=5&start={a}&end={b}&limit=1000"
    for i in range(8):
        try:
            with rl:
                with urllib.request.urlopen(url, timeout=30) as r:
                    j = json.loads(r.read())
                time.sleep(0.1)
            if j.get("retCode") == 0:
                break
        except Exception:
            pass
        time.sleep(2 + 3 * i)
    else:
        print("fail", c, flush=True)
        return
    with lock:
        w.writerows([s, x[0], x[1], x[4]] for x in j["result"]["list"])
        done[0] += 1
        if done[0] % 1000 == 0:
            print(done[0], flush=True)


with ThreadPoolExecutor(8) as ex:
    list(ex.map(job, chunks))
out.close()
print("done", flush=True)

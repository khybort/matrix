"""Pick the tick panel: top 40 crypto USDT perps by Bybit turnover over 2026-04-01..04-30
(round 1's 1h klines), launched before 2026-03-01. Usage: python3 panel.py <r1_data_dir> <out.txt>"""
import json, sys
import pandas as pd

d, out = sys.argv[1], sys.argv[2]
ins = {x["symbol"]: x for x in json.load(open(f"{d}/linear_instr.json"))["result"]["list"]}
k = pd.read_csv(f"{d}/kline_1h.csv", usecols=["symbol", "ts", "turnover"])
a, b = pd.Timestamp("2026-04-01", tz="UTC").value // 10**6, pd.Timestamp("2026-05-01", tz="UTC").value // 10**6
k = k[(k.ts >= a) & (k.ts < b)]
tv = k.groupby("symbol").turnover.sum().sort_values(ascending=False)
cut = pd.Timestamp("2026-03-01", tz="UTC").value // 10**6
keep = [s for s in tv.index if s in ins and ins[s]["symbolType"] == "" and int(ins[s]["launchTime"]) < cut][:40]
open(out, "w").write("\n".join(keep) + "\n")
print(len(keep), keep)

"""H9 settlement events (no returns): every settlement of a crypto perp listed >= 30 days with
trailing-7d mean daily turnover >= $2M where |F| >= 0.10 % or |F^| >= 0.10 %.
  python3 -I h9_events.py <r1_data_dir> <out.csv>"""
import json, sys
import numpy as np
import pandas as pd

d, out = sys.argv[1], sys.argv[2]
H = 3_600_000
ins = {x["symbol"]: x for x in json.load(open(f"{d}/linear_instr.json"))["result"]["list"]}
ok = {s for s, x in ins.items() if x["symbolType"] in ("", "innovation")}
f = pd.read_csv(f"{d}/funding.csv")
f = f[f.symbol.isin(ok)].sort_values(["symbol", "ts"])
f["ts"] = (f.ts // 1000) * 1000
f["prev_ts"] = f.groupby("symbol").ts.shift()
f["prev_rate"] = f.groupby("symbol").rate.shift()
f["interval_h"] = ((f.ts - f.prev_ts) / H).round()
f = f.dropna(subset=["prev_ts"])
f = f[f.interval_h.isin([1, 2, 4, 8])]
k = pd.read_csv(f"{d}/kline_1h.csv", usecols=["symbol", "ts", "turnover"]).sort_values(["symbol", "ts"])
k["tv7"] = k.groupby("symbol").turnover.transform(lambda s: s.rolling(168, min_periods=168).sum() / 7)
# turnover known at T: bars that closed by T -> bar ts = T-1h
k["ts"] = k.ts + H
f = f.merge(k[["symbol", "ts", "tv7"]], on=["symbol", "ts"], how="left")
p = pd.read_csv(f"{d}/premium_1h.csv", usecols=["symbol", "ts", "close"])
p = p[p.symbol.isin(set(f.symbol))]
pm = {s: g.set_index("ts").close for s, g in p.groupby("symbol")}
fh = []
for r in f.itertuples():
    if r.interval_h == 1:
        fh.append(r.prev_rate)
        continue
    s = pm.get(r.symbol)
    if s is None:
        fh.append(np.nan)
        continue
    ts = np.arange(r.ts - r.interval_h * H, r.ts - 2 * H + 1, H)
    v = s.reindex(ts).dropna()
    if v.empty:
        fh.append(np.nan)
        continue
    P = v.mean()
    i = 0.0001 * r.interval_h / 8
    fh.append(P + min(max(i - P, -0.0005), 0.0005))
f["fhat"] = fh
f["launch"] = f.symbol.map(lambda s: int(ins[s]["launchTime"]))
f = f[(f.ts - f.launch >= 30 * 24 * H) & (f.tv7 >= 2e6)]
print("universe settlements", len(f), "corr(F^,F)", f[["fhat", "rate"]].corr().iloc[0, 1])
e = f[(f.rate.abs() >= 0.001) | (f.fhat.abs() >= 0.001)]
e[["symbol", "ts", "interval_h", "rate", "fhat", "tv7"]].to_csv(out, index=False)
print("events", len(e), "|F|>=.1%", (e.rate.abs() >= .001).sum(), "|F^|>=.1%", (e.fhat.abs() >= .001).sum(),
      "|F|>=.3%", (e.rate.abs() >= .003).sum(), "|F^|>=.3%", (e.fhat.abs() >= .003).sum())

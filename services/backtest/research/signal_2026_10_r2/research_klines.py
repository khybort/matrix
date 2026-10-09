"""Kline hypotheses H9 (settlement microstructure, 5m), H11 (OI shock, 1h), H12 (new-listing short).
  python3 -I research_klines.py counts|train <r2_dir> <r1_data_dir>
  python3 -I research_klines.py holdout <r2_dir> <r1_data_dir> CELL [CELL ...]
  python3 -I research_klines.py early <r2_dir> <early_data_dir> H11-flush-4h   # H11c confirmation
Writes <r2_dir>/episodes_klines_<mode>.csv."""
import json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stats import summary  # noqa: E402

H, M = 3_600_000, 60_000
SPLIT = pd.Timestamp("2026-06-01", tz="UTC").value // 10**6
START = pd.Timestamp("2025-10-01", tz="UTC").value // 10**6
END = pd.Timestamp("2026-10-09", tz="UTC").value // 10**6
EARLY = (pd.Timestamp("2024-10-01", tz="UTC").value // 10**6, pd.Timestamp("2025-10-01", tz="UTC").value // 10**6)
FEE = 11.0
CELLS = ["H9-pre-10", "H9-pre-30", "H9-post-10-30", "H9-post-10-240", "H9-post-30-30", "H9-post-30-240",
         "H11-build-4h", "H11-build-24h", "H11-flush-4h", "H11-flush-24h", "H12-7d", "H12-14d"]


def spread(sm, tv):
    return np.maximum(2.0, np.exp(sm["a"] + sm["b"] * np.log(np.maximum(tv, 1.0))))


def in_split(ts, mode):
    if mode == "early":  # H11c confirmation period, see PREREG addendum
        return (ts >= EARLY[0]) & (ts < EARLY[1])
    return (ts >= START) & (ts < SPLIT) if mode in ("train", "counts") else (ts >= SPLIT) & (ts < END)


def nonoverlap(df):
    keep, last = [], {}
    for r in df.sort_values("entry").itertuples():
        k = (r.cell, r.symbol)
        if r.entry >= last.get(k, -1):
            keep.append(r.Index)
            last[k] = r.exit
    return df.loc[keep]


def fund_pnl(fund, sym, side, a, b):
    s = fund.get(sym)
    if s is None:
        return 0.0
    i, j = np.searchsorted(s.index.values, [a, b], side="left")
    return float(-side * s.values[i:j].sum() * 1e4)


def h9(r2, sm):
    e = pd.read_csv(f"{r2}/h9_events.csv")
    k = pd.read_csv(f"{r2}/k5m.csv").drop_duplicates(["symbol", "ts"])
    px = {s: g.set_index("ts").close.sort_index() for s, g in k.groupby("symbol")}
    rows = []
    for r in e.itertuples():
        s = px.get(r.symbol)
        if s is None:
            continue
        g = lambda ts: s.get(ts, np.nan)  # noqa: E731
        sp = float(spread(sm, r.tv7))
        for th, tag in ((0.001, "10"), (0.003, "30")):
            if abs(r.fhat) >= th:
                rows.append((f"H9-pre-{tag}", r.symbol, r.ts - 30 * M, r.ts, -np.sign(r.fhat), g(r.ts - 35 * M), g(r.ts - 5 * M), sp))
            if abs(r.rate) >= th:
                for hold in (30, 240):
                    rows.append((f"H9-post-{tag}-{hold}", r.symbol, r.ts + 5 * M, r.ts + (5 + hold) * M, np.sign(r.rate),
                                 g(r.ts), g(r.ts + hold * M), sp))
    return pd.DataFrame(rows, columns=["cell", "symbol", "entry", "exit", "side", "pe", "px", "spread"])


def h11_h12(d1, sm, ins):
    k = pd.read_csv(f"{d1}/kline_1h.csv", usecols=["symbol", "ts", "close", "turnover"]).sort_values(["symbol", "ts"])
    ok = {s for s, x in ins.items() if x["symbolType"] in ("", "innovation")}
    k = k[k.symbol.isin(ok)]
    k["end"] = k.ts + H
    oi = pd.read_csv(f"{d1}/oi_1h.csv")
    rows = []
    for sym, g in k.groupby("symbol"):
        g = g.set_index("end")
        c = g.close
        tv7 = g.turnover.rolling(168, min_periods=168).sum() / 7
        launch = int(ins[sym]["launchTime"])
        # H12
        if pd.Timestamp("2025-10-01", tz="UTC").value // 10**6 <= launch <= pd.Timestamp("2026-09-24", tz="UTC").value // 10**6:
            ent = -(-(launch + 25 * H) // H) * H
            tv24 = g.turnover[(g.index > launch) & (g.index <= launch + 24 * H + H)].sum()
            for days, tag in ((7, "7d"), (14, "14d")):
                ex = ent + days * 24 * H
                rows.append((f"H12-{tag}", sym, ent, ex, -1, c.get(ent, np.nan), c.get(ex, np.nan), float(spread(sm, tv24))))
        # H11
        o = oi[oi.symbol == sym]
        if o.empty:
            continue
        o = o.set_index("ts").oi.sort_index()
        o = o[~o.index.duplicated()]
        idx = c.index.values
        oiv = o.reindex(idx).to_numpy(float)
        oi4 = o.reindex(idx - 4 * H).to_numpy(float)
        cv = c.to_numpy(float)
        c4 = c.reindex(idx - 4 * H).to_numpy(float)
        with np.errstate(all="ignore"):
            doi = np.log(oiv / oi4)
            r = np.log(cv / c4)
        tvv = tv7.to_numpy(float)
        elig = (idx - launch >= 30 * 24 * H) & (tvv >= 2e6) & (np.abs(r) >= 0.03)
        for kind, cond, mult in (("build", doi >= 0.15, 1), ("flush", doi <= -0.15, -1)):
            for t in np.nonzero(elig & cond)[0]:
                T = idx[t]
                ent = T + H
                for hh, tag in ((4, "4h"), (24, "24h")):
                    ex = ent + hh * H
                    rows.append((f"H11-{kind}-{tag}", sym, ent, ex, mult * np.sign(r[t]), c.get(ent, np.nan), c.get(ex, np.nan),
                                 float(spread(sm, tvv[t]))))
    return pd.DataFrame(rows, columns=["cell", "symbol", "entry", "exit", "side", "pe", "px", "spread"])


def main():
    mode, r2, d1 = sys.argv[1], sys.argv[2], sys.argv[3]
    cells = sys.argv[4:] if mode in ("holdout", "early") else CELLS
    sm = json.load(open(f"{r2}/spreadmap.json"))
    ins = {x["symbol"]: x for x in json.load(open(f"{d1}/linear_instr.json"))["result"]["list"]}
    parts = [h11_h12(d1, sm, ins)] if mode == "early" else [h9(r2, sm), h11_h12(d1, sm, ins)]
    df = pd.concat(parts, ignore_index=True)
    df = df[df.cell.isin(cells) & in_split(df.entry, mode)].dropna(subset=["pe", "px"])
    df = nonoverlap(df)
    if mode == "counts":
        print(df.groupby("cell").size().reindex(cells).to_string())
        return
    f = pd.read_csv(f"{d1}/funding.csv")
    fund = {s: g.set_index("ts").rate.sort_index() for s, g in f.groupby("symbol")}
    df["gross"] = df.side * (df.px / df.pe - 1) * 1e4
    df["cost"] = FEE + df.spread
    # position held at a settlement S if entry <= S < exit (entry/exit = trade times)
    df["funding"] = [fund_pnl(fund, r.symbol, r.side, r.entry, r.exit) for r in df.itertuples()]
    df["net"] = df.gross - df.cost + df.funding
    df.to_csv(f"{r2}/episodes_klines_{mode}.csv", index=False)
    df["day"] = pd.to_datetime(df.entry, unit="ms").dt.date
    df["week"] = pd.to_datetime(df.entry, unit="ms").dt.strftime("%G-%V")
    for cell in cells:
        g = df[df.cell == cell]
        if g.empty:
            print(cell, "no episodes")
            continue
        s = summary(g.net, g.day, g.week)
        print(f"{cell:16s} n={s['n']:5d} days={s['days']:3d} gross={g.gross.mean():+7.1f} cost={g.cost.mean():5.1f} "
              f"fund={g.funding.mean():+6.1f} net={s['mean']:+7.1f} med={s['median']:+7.1f} t_day={s['t_day']:+5.2f} "
              f"t_wk={s['t_week']:+5.2f}")


if __name__ == "__main__":
    main()

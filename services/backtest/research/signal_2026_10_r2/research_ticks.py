"""Tick-panel hypotheses H8a-flow, H8a-large, H8b, H10 (see PREREG.txt).
  python3 -I research_ticks.py counts  <r2_dir> <r1_data_dir>     # trigger counts only, no returns
  python3 -I research_ticks.py train   <r2_dir> <r1_data_dir>
  python3 -I research_ticks.py holdout <r2_dir> <r1_data_dir> CELL [CELL ...]
Writes <r2_dir>/episodes_ticks_<mode>.csv (one row per episode)."""
import datetime as dt, json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stats import summary  # noqa: E402

COLS = ["o", "h", "l", "c", "buy", "sell", "nb", "ns", "lb10k", "ls10k", "lb50k", "ls50k",
        "lb250k", "ls250k", "lb1m", "ls1m", "spr"]
C = {k: i for i, k in enumerate(COLS)}
D0 = dt.date(2026, 4, 24)
D1 = dt.date(2026, 10, 8)
NDAY = (D1 - D0).days + 1
T0 = pd.Timestamp(D0, tz="UTC").value // 10**9
TRAIN = (pd.Timestamp("2026-05-01", tz="UTC").value // 10**9, pd.Timestamp("2026-08-01", tz="UTC").value // 10**9)
HOLD = (TRAIN[1], pd.Timestamp("2026-10-09", tz="UTC").value // 10**9)
FEE = 11.0
W7 = 7 * 1440

CELLS = {
    "H8a-flow-follow-60": ("flow", 1, 60), "H8a-flow-follow-240": ("flow", 1, 240),
    "H8a-flow-fade-60": ("flow", -1, 60), "H8a-flow-fade-240": ("flow", -1, 240),
    "H8a-large-follow-60": ("large", 1, 60), "H8a-large-follow-240": ("large", 1, 240),
    "H8a-large-fade-60": ("large", -1, 60), "H8a-large-fade-240": ("large", -1, 240),
    "H8b-1m-30": ("casc1", -1, 30), "H8b-1m-60": ("casc1", -1, 60), "H8b-1m-240": ("casc1", -1, 240),
    "H8b-5m-30": ("casc5", -1, 30), "H8b-5m-60": ("casc5", -1, 60), "H8b-5m-240": ("casc5", -1, 240),
    "H10-15": ("lead", 1, 15), "H10-60": ("lead", 1, 60),
}


def load(r2, sym):
    a = np.full((NDAY * 1440, len(COLS)), np.nan)
    z = np.load(f"{r2}/bars_npz/{sym}.npz") if os.path.exists(f"{r2}/bars_npz/{sym}.npz") else None
    for i in range(NDAY):
        day = (D0 + dt.timedelta(i)).isoformat()
        p = f"{r2}/bars/{sym}/{day}.npy"
        b = z[day] if z is not None and day in z.files else (np.load(p) if os.path.exists(p) else None)
        if b is not None:
            sp = b[:, C["spr"]]
            if np.isfinite(sp).any():
                b[np.isnan(sp), C["spr"]] = np.nanmedian(sp)
            a[i * 1440:(i + 1) * 1440] = b
    return a


def roll_sum(x, w):
    c = np.concatenate([[0.0], np.nancumsum(x)])
    out = np.full(len(x), np.nan)
    out[w - 1:] = c[w:] - c[:-w]
    return out


def signals(a, sym, r2, bucket, bn):
    """Return {kind: signed trigger array (+1/-1/0)} using information up to and including minute t."""
    close = pd.Series(a[:, C["c"]]).ffill(limit=5).to_numpy()
    buy, sell = np.nan_to_num(a[:, C["buy"]]), np.nan_to_num(a[:, C["sell"]])
    vol = buy + sell
    have = np.isfinite(a[:, C["c"]]) | (vol == 0) & np.isfinite(a[:, C["spr"]])
    out = {}
    # H8a-flow
    B15, S15 = roll_sum(buy, 15), roll_sum(sell, 15)
    V15 = B15 + S15
    I = np.where(V15 > 0, (B15 - S15) / np.where(V15 > 0, V15, 1), 0)
    vbar = pd.Series(vol).rolling(W7, min_periods=W7 // 2).mean().shift(15).to_numpy() * 15
    out["flow"] = np.where((np.abs(I) >= 0.35) & (V15 >= 4 * vbar), np.sign(I), 0)
    # H8a-large
    L = roll_sum(np.nan_to_num(a[:, C[f"lb{bucket}"]]) - np.nan_to_num(a[:, C[f"ls{bucket}"]]), 15)
    sig = pd.Series(L).rolling(W7, min_periods=W7 // 2).std().shift(15).to_numpy()
    out["large"] = np.where((np.abs(L) >= 5 * sig) & (sig > 0), np.sign(L), 0)
    # H8b 1m
    lc = np.log(close)
    r1 = np.diff(lc, prepend=np.nan)
    s1 = pd.Series(r1).rolling(1440, min_periods=720).std().shift(1).to_numpy()
    v1 = pd.Series(vol).rolling(1440, min_periods=720).mean().shift(1).to_numpy()
    share_dn = np.where(vol > 0, sell / np.where(vol > 0, vol, 1), 0)
    share1 = np.where(r1 < 0, share_dn, 1 - share_dn)
    c1 = (np.abs(r1) >= np.maximum(0.01, 6 * s1)) & (share1 >= 0.70) & (vol >= 8 * v1)
    out["casc1"] = np.where(c1, np.sign(r1), 0)
    # H8b 5m
    r5 = lc - np.concatenate([np.full(5, np.nan), lc[:-5]])
    s5 = pd.Series(r5).rolling(1440, min_periods=720).std().shift(5).to_numpy()
    V5, S5 = roll_sum(vol, 5), roll_sum(sell, 5)
    sh5d = np.where(V5 > 0, S5 / np.where(V5 > 0, V5, 1), 0)
    share5 = np.where(r5 < 0, sh5d, 1 - sh5d)
    c5 = (np.abs(r5) >= np.maximum(0.02, 5 * s5)) & (share5 >= 0.65) & (V5 >= 5 * 5 * v1)
    out["casc5"] = np.where(c5, np.sign(r5), 0)
    # H10
    if bn is not None:
        lb = np.log(bn)
        g = (lb - np.concatenate([np.full(5, np.nan), lb[:-5]])) - r5
        out["lead"] = np.where(np.abs(g) >= 0.0030, np.sign(g), 0)
    for k in out:
        out[k] = np.nan_to_num(out[k]).astype(int)
    return out, close


def binance(r2, sym):
    p = f"{r2}/bn/{sym}.csv"
    if not os.path.exists(p) or os.path.getsize(p) == 0:
        return None
    b = pd.read_csv(p, header=None, names=["t", "c"])
    idx = (b.t // 1000 - T0) // 60
    arr = np.full(NDAY * 1440, np.nan)
    ok = (idx >= 0) & (idx < len(arr))
    arr[idx[ok].to_numpy()] = b.c[ok].to_numpy()
    return pd.Series(arr).ffill(limit=2).to_numpy()


def episodes(trig, side_mult, H, close, spr, fund, lo, hi, sym, cell, count_only):
    rows, nxt = [], 0
    n = len(close)
    idx = np.nonzero(trig)[0]
    for t in idx:
        if t < nxt:
            continue
        e, x = t + 1, t + 1 + H
        if x >= n:
            break
        te = T0 + 60 * e
        if not (lo <= te < hi):
            continue
        if not (np.isfinite(close[e]) and np.isfinite(close[x])):
            continue
        nxt = x + 1
        side = side_mult * trig[t]
        if count_only:
            rows.append((cell, sym, te, side))
            continue
        gross = side * (close[x] / close[e] - 1) * 1e4
        cost = FEE + np.nan_to_num(spr[e], nan=10) / 2 + np.nan_to_num(spr[x], nan=10) / 2
        e_end, x_end = T0 + 60 * (e + 1), T0 + 60 * (x + 1)
        fr = 0.0
        if fund is not None:
            m = (fund.index >= e_end) & (fund.index < x_end)
            fr = -side * fund[m].sum() * 1e4
        rows.append((cell, sym, te, side, gross, cost, fr, gross - cost + fr))
    return rows


def main():
    mode, r2, d1 = sys.argv[1], sys.argv[2], sys.argv[3]
    cells = sys.argv[4:] if mode == "holdout" else list(CELLS)
    lo, hi = TRAIN if mode in ("train", "counts") else HOLD
    syms = open(f"{r2}/panel.txt").read().split()
    f = pd.read_csv(f"{d1}/funding.csv")
    f = f[f.symbol.isin(syms)]
    funds = {s: g.assign(ts=g.ts // 1000).set_index("ts").rate.sort_index() for s, g in f.groupby("symbol")}
    # large bucket per symbol: smallest bucket with train share <= 15 %
    buckets = {}
    rows = []
    for sym in syms:
        a = load(r2, sym)
        i0, i1 = (TRAIN[0] - T0) // 60, (TRAIN[1] - T0) // 60
        tot = np.nansum(a[i0:i1, C["buy"]] + a[i0:i1, C["sell"]])
        bucket = "1m"
        for b in ("10k", "50k", "250k", "1m"):
            if np.nansum(a[i0:i1, C[f"lb{b}"]] + a[i0:i1, C[f"ls{b}"]]) <= 0.15 * tot:
                bucket = b
                break
        buckets[sym] = bucket
        sig, close = signals(a, sym, r2, bucket, binance(r2, sym))
        for cell in cells:
            kind, mult, H = CELLS[cell]
            if kind not in sig:
                continue
            rows += episodes(sig[kind], mult, H, close, a[:, C["spr"]], funds.get(sym), lo, hi, sym, cell, mode == "counts")
        print(sym, bucket, len(rows), flush=True)
    if mode == "counts":
        df = pd.DataFrame(rows, columns=["cell", "symbol", "t", "side"])
        print(df.groupby("cell").size().reindex(cells).to_string())
        print(json.dumps(buckets))
        return
    df = pd.DataFrame(rows, columns=["cell", "symbol", "t", "side", "gross", "cost", "funding", "net"])
    df.to_csv(f"{r2}/episodes_ticks_{mode}.csv", index=False)
    df["day"] = pd.to_datetime(df.t, unit="s").dt.date
    for cell in cells:
        g = df[df.cell == cell]
        if g.empty:
            print(cell, "no episodes")
            continue
        s = summary(g.net, g.day)
        print(f"{cell:22s} n={s['n']:5d} days={s['days']:3d} gross={g.gross.mean():+7.1f} cost={g.cost.mean():5.1f} "
              f"fund={g.funding.mean():+5.1f} net={s['mean']:+7.1f} med={s['median']:+7.1f} t_day={s['t_day']:+5.2f}")


if __name__ == "__main__":
    main()

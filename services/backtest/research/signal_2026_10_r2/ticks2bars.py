"""Reduce Bybit public trade archive files to 1-minute bars; raw files are deleted after reduction.

  python3 -I ticks2bars.py panel <panel.txt> <first_day> <last_day> <out_dir> <tmp_dir> [reuse_dir]
  python3 -I ticks2bars.py local <tick_dir> <out_dir>      # reduce already-downloaded files, keep them

Per minute: o h l c, buy/sell aggressor USD, print counts, aggressor USD in prints >= 10k/50k/250k/1M
per side, median spread proxy (last buy print vs last sell print, both <= 10 s old, ask > bid), bps.
RPI prints are dropped (retail-only liquidity).
"""
import datetime as dt, gzip, os, sys, time, urllib.request
from multiprocessing import Pool
import numpy as np
import pandas as pd

COLS = ["o", "h", "l", "c", "buy", "sell", "nb", "ns", "lb10k", "ls10k", "lb50k", "ls50k",
        "lb250k", "ls250k", "lb1m", "ls1m", "spr"]
BUCKETS = [(10_000, "10k"), (50_000, "50k"), (250_000, "250k"), (1_000_000, "1m")]


def reduce_file(path, day):
    df = pd.read_csv(path, compression="gzip")
    if "RPI" in df.columns:
        df = df[df["RPI"] != 1]
    df = df.sort_values("timestamp", kind="stable")
    t = df["timestamp"].to_numpy(float)
    px = df["price"].to_numpy(float)
    usd = df["foreignNotional"].to_numpy(float)
    buy = (df["side"].to_numpy() == "Buy")
    day0 = pd.Timestamp(day, tz="UTC").value / 1e9
    m = np.floor((t - day0) / 60).astype(int)
    ok = (m >= 0) & (m < 1440)
    t, px, usd, buy, m = t[ok], px[ok], usd[ok], buy[ok], m[ok]
    # spread proxy
    s = pd.DataFrame({"t": t, "px": px, "buy": buy})
    s["ask"] = s.px.where(s.buy).ffill()
    s["ta"] = s.t.where(s.buy).ffill()
    s["bid"] = s.px.where(~s.buy).ffill()
    s["tb"] = s.t.where(~s.buy).ffill()
    good = (s.t - s.ta <= 10) & (s.t - s.tb <= 10) & (s.ask > s.bid)
    spr = ((s.ask - s.bid) / ((s.ask + s.bid) / 2) * 1e4).where(good)
    out = np.full((1440, len(COLS)), np.nan)
    g = pd.DataFrame({"m": m, "px": px, "usd": usd, "buy": buy, "spr": spr.to_numpy()})
    gb = g.groupby("m")
    out[:, 0][gb.px.first().index] = gb.px.first().to_numpy()
    out[:, 1][gb.px.max().index] = gb.px.max().to_numpy()
    out[:, 2][gb.px.min().index] = gb.px.min().to_numpy()
    out[:, 3][gb.px.last().index] = gb.px.last().to_numpy()
    out[:, 4:16] = 0.0
    out[:, 16][gb.spr.median().index] = gb.spr.median().to_numpy()
    for side, cb, cn, off in ((True, 4, 6, 0), (False, 5, 7, 1)):
        sub = g[g.buy == side]
        sg = sub.groupby("m")
        out[sg.usd.sum().index, cb] = sg.usd.sum().to_numpy()
        out[sg.usd.count().index, cn] = sg.usd.count().to_numpy()
        for j, (thr, _) in enumerate(BUCKETS):
            big = sub[sub.usd >= thr].groupby("m").usd.sum()
            out[big.index, 8 + 2 * j + off] = big.to_numpy()
    return out.astype(np.float64)


def fetch(sym, day, tmp):
    url = f"https://public.bybit.com/trading/{sym}/{sym}{day}.csv.gz"
    p = os.path.join(tmp, f"{sym}{day}.csv.gz")
    for a in range(6):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(p, "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            with gzip.open(p) as fh:  # 404s come back as HTML
                fh.read(64)
            return p
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(3 + 5 * a)
        except Exception:
            time.sleep(3 + 5 * a)
    return None


def job(args):
    sym, day, out_dir, tmp, reuse = args
    dst = os.path.join(out_dir, sym, f"{day}.npy")
    if os.path.exists(dst):
        return sym, day, "have"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    local = os.path.join(reuse, f"{sym}{day}.csv.gz") if reuse else None
    if local and os.path.exists(local):
        p, own = local, False
    else:
        p, own = fetch(sym, day, tmp), True
    if p is None:
        return sym, day, "missing"
    try:
        np.save(dst, reduce_file(p, day))
        st = "ok"
    except Exception as e:
        st = f"err {e!r}"
    if own:
        os.remove(p)
    return sym, day, st


def main():
    mode = sys.argv[1]
    if mode == "panel":
        syms = open(sys.argv[2]).read().split()
        d0, d1 = dt.date.fromisoformat(sys.argv[3]), dt.date.fromisoformat(sys.argv[4])
        out_dir, tmp = sys.argv[5], sys.argv[6]
        reuse = sys.argv[7] if len(sys.argv) > 7 else None
        days = [(d0 + dt.timedelta(i)).isoformat() for i in range((d1 - d0).days + 1)]
        jobs = [(s, d, out_dir, tmp, reuse) for d in days for s in syms]
    else:
        src, out_dir = sys.argv[2], sys.argv[3]
        jobs = []
        for f in sorted(os.listdir(src)):
            if f.endswith(".csv.gz"):
                jobs.append((f[:-17], f[-17:-7], out_dir, None, src))
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    with Pool(int(os.environ.get("WORKERS", "6"))) as pool:
        for sym, day, st in pool.imap_unordered(job, jobs):
            n += 1
            if st not in ("ok", "have") or n % 200 == 0:
                print(n, len(jobs), sym, day, st, flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()

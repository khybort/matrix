"""Fit log(spread_bps) = a + b log(daily turnover USD) on every reduced tick symbol-day.
  python3 -I spreadmap.py <out.json> <bars_dir> [<bars_dir> ...]"""
import json, os, sys
import numpy as np

pts = []
for d in sys.argv[2:]:
    for sym in os.listdir(d):
        if sym.endswith(".npz"):  # compressed form: one file per symbol, one array per day
            z = np.load(os.path.join(d, sym))
            items = [(f, z[f]) for f in z.files]
        else:
            items = [(f, np.load(os.path.join(d, sym, f))) for f in os.listdir(os.path.join(d, sym))]
        for f, b in items:
            tv = np.nansum(b[:, 4] + b[:, 5])
            sp = np.nanmedian(b[:, 16]) if np.isfinite(b[:, 16]).any() else np.nan
            if tv > 0 and sp > 0:
                pts.append((sym, f[:10], tv, sp))
x = np.log([p[2] for p in pts])
y = np.log([p[3] for p in pts])
b, a = np.polyfit(x, y, 1)
res = y - (a + b * x)
out = {"a": a, "b": b, "n": len(pts), "symbols": len({p[0] for p in pts}), "resid_sd": float(res.std())}
for tv in (1e6, 5e6, 2e7, 1e8, 1e9):
    out[f"spread_at_{tv:.0e}"] = float(np.exp(a + b * np.log(tv)))
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(json.dumps(out, indent=1))

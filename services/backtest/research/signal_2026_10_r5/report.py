"""Round 5 descriptive extras (record only, after the decision): per-asset, trimmed, cost x2, frequency.

usage: python3 report.py <data dir>     (needs episodes.pkl from `research.py record`)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(sys.argv[1])
e = pd.read_pickle(D / "episodes.pkl")
f = pd.read_pickle(D / "features.pkl")
years = {"train": (pd.Timestamp("2024-07-01") - pd.Timestamp("2021-07-01")).days / 365,
         "holdout": (pd.Timestamp("2026-10-09") - pd.Timestamp("2024-07-01")).days / 365}


def trim(x, q=0.05):
    lo, hi = x.quantile(q), x.quantile(1 - q)
    return x[(x >= lo) & (x <= hi)].mean()


rows = []
for (c, sp), g in e.groupby(["cell", "split"], sort=False):
    r = {"cell": c, "split": sp, "n": len(g), "per_yr": round(len(g) / years[sp], 1),
         "gross": round(g.gross.mean(), 1), "net": round(g.net.mean(), 1),
         "net_cost2": round((g.net - g.cost).mean(), 1), "trim5": round(trim(g.net), 1),
         "median": round(g.net.median(), 1), "win": round((g.net > 0).mean(), 2),
         "BTC": round(g[g.asset == "BTC"].net.mean(), 1), "ETH": round(g[g.asset == "ETH"].net.mean(), 1),
         "fund": round(g.funding.mean(), 1), "cost": round(g.cost.mean(), 2)}
    rows.append(r)
r = pd.DataFrame(rows)
print(r.to_string(index=False))
r.to_csv(D / "report.csv", index=False)

# feature coverage and unconditional drift (the benchmark a long-only cell must beat)
print("\nfeature coverage (non-missing pct days):")
print(f.groupby("asset")[[c for c in f.columns if c.startswith("pct_")]].count())
for a in ("BTC", "ETH"):
    print(a, "first day with each pct:", {c: str(pd.to_datetime(f[(f.asset == a) & f[c].notna()].D.min(), unit="ms").date())
                                          for c in f.columns if c.startswith("pct_")})
print("\nfeature correlations (BTC):")
print(f[f.asset == "BTC"][["VRP", "RR25", "TERM", "PC3", "DVOLchg"]].corr(method="spearman").round(2))
_ = np

# unconditional long benchmark: every day's 00:00 decision, entry +1h, same hold, price only (gross, before funding)
sys.argv = [sys.argv[0], str(D), "none"]
src = (Path(__file__).parent / "research.py").read_text().split("if MODE ==")[0]
ns: dict = {}
exec(src, ns)  # noqa: S102 — reuse the frozen loaders
for a, sym in ns["ASSETS"].items():
    px = ns["closes"](sym)
    for hold in (3, 7):
        out = {}
        for sp, (s0, s1) in (("train", ("2021-07-01", "2024-07-01")), ("holdout", ("2024-07-01", "2026-10-01"))):
            ds = pd.date_range(s0, s1, freq="D", tz="UTC", inclusive="left")
            v = [px.get(int(d.timestamp() * 1000) + 3600_000 + hold * 86_400_000) / px.get(int(d.timestamp() * 1000) + 3600_000) - 1
                 for d in ds]
            out[sp] = round(np.nanmean(v) * 1e4, 1)
        print(f"unconditional long {a} {hold}d price bps:", out)

"""Run the repo's promotion statistics (edge_study BY, promotion.deflated_sharpe) on research
results. Runs inside the backtest image: python bar.py <results.json> <series.json-out>
Input: research results JSON. Uses 'net' per period, or H1 'net' over executable episodes.
"""
import json, sys

from matrix_shared.edge_study import benjamini_yekutieli, two_sided_p
from matrix_shared.promotion import deflated_sharpe

M = 38
res = json.load(open(sys.argv[1]))
rows = []
for k, v in res.items():
    per = v["periods"]
    if k.startswith("H1"):
        xs = [p["net"] for p in per if p.get("exec")]
        xs3 = [p["net3"] for p in per if p.get("exec")]
    else:
        xs, xs3 = [p["net"] for p in per], None
    n = len(xs)
    if n < 2:
        continue
    m = sum(xs) / n
    sd = (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5
    t = m / sd * n ** 0.5 if sd else 0.0
    d = deflated_sharpe(xs, n_trials=M)
    rows.append({"h": k, "n": n, "mean": round(m, 2), "t": round(t, 2), "p": two_sided_p(t),
                 "dsr": round(d["dsr"], 3) if d else None,
                 "mean_borrow_x3": round(sum(xs3) / n, 2) if xs3 else None})
# BY over the declared family: untested members count as p=1
prim = [r for r in rows if r["h"] not in ("H1.flip", "H1.optimistic")]
ps = [r["p"] if r["t"] > 0 else 1.0 for r in prim]
ps += [1.0] * max(0, M - len(ps))
keep = benjamini_yekutieli(ps)
for r, k in zip(prim, keep):
    r["by_pass"] = k
for r in sorted(rows, key=lambda r: -r["t"]):
    print(json.dumps(r))

"""BY q-values over the m train p-values (one-sided, day-clustered t).  python3 -I by_table.py <r2_dir>"""
import os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stats import by_qvalues, clustered_t, p_one_sided  # noqa: E402

r2 = sys.argv[1]
rows = []
for f, tcol, unit in (("episodes_ticks_train.csv", "t", "s"), ("episodes_klines_train.csv", "entry", "ms")):
    d = pd.read_csv(f"{r2}/{f}")
    d["day"] = pd.to_datetime(d[tcol], unit=unit).dt.date
    for c, g in d.groupby("cell", sort=False):
        t = clustered_t(g.net, g.day)
        rows.append((c, len(g), g.gross.mean(), g.net.mean(), t, p_one_sided(t)))
r = pd.DataFrame(rows, columns=["cell", "n", "gross", "net", "t_day", "p"])
r["q_BY"] = by_qvalues(r.p)
print(len(r), "cells")
print(r.round(3).to_string(index=False))

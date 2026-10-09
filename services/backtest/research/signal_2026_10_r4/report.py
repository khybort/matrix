"""Round 4 report. Usage: python3 report.py <data_dir> train|holdout|record
 train   : train table for all 66 cells at the primary size, train passers listed.
 holdout : holdout for train passers only (+ BY over the programme, m = 157).
 record  : everything else (holdouts of failures, sizes, risk, tracking, quarters) — for the record."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.argv, mode = sys.argv[:2], sys.argv[2]
import research as R  # noqa: E402

D = Path(sys.argv[1])
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500); pd.set_option("display.max_columns", 40)
M = R.M_PRIOR + 66
H1_P = 1e-12  # round 1 H1 holdout p (t 10.2): effectively zero


def by_q(p_round4):
    ps = np.array([H1_P] + [1.0] * (R.M_PRIOR - 1) + list(p_round4) + [1.0] * (66 - len(p_round4)))
    order = np.argsort(ps); m = len(ps); c = sum(1 / i for i in range(1, m + 1))
    q = np.empty(m); prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, ps[i] * m * c / rank); q[i] = prev
    return q[R.M_PRIOR: R.M_PRIOR + len(p_round4)]


def fmt(c):
    cols = ["cell", "n", "weeks", "mean_e", "median_e", "t_wk", "t_contract", "contracts", "mw_excess", "mean_ann",
            "mean_rf", "mean_e5", "liq", "mean_hold"]
    c = c[cols].copy()
    for k in ["mean_e", "median_e", "mw_excess", "mean_ann", "mean_rf", "mean_e5", "liq"]:
        c[k] = (c[k] * 100).round(2)
    for k in ["t_wk", "t_contract", "mean_hold"]:
        c[k] = c[k].round(2)
    return c.to_string(index=False)


df = pd.read_pickle(D / f"tranches_{R.PRIMARY}.pkl")
cells = R.cells(df)
tr = cells[cells.split == "train"].reset_index(drop=True)
passers = tr[(tr.mean_e > 0) & (tr.t_wk >= 2)].cell.tolist()
if mode == "train":
    print(fmt(tr))
    print("\nTRAIN PASSERS:", len(passers), passers)
elif mode == "holdout":
    ho = cells[(cells.split == "holdout") & cells.cell.isin(passers)].reset_index(drop=True)
    ho["q_BY"] = by_q(ho.p.fillna(1.0).tolist())
    print(fmt(ho)); print(ho[["cell", "p", "q_BY"]].to_string(index=False))
    ok = ho[(ho.mean_e > 0) & (ho.t_wk >= 2) & (ho.q_BY <= 0.05)]
    print("\nHOLDOUT SURVIVORS:", len(ok), ok.cell.tolist())
else:
    ho = cells[cells.split == "holdout"].reset_index(drop=True)
    print("ALL HOLDOUT (record)\n" + fmt(ho))
    ex = df[df.excluded != ""]
    print("\nexcluded:", ex.groupby(["group", "exit", "excluded"]).size().to_string())
    for size in R.SIZES:
        d = pd.read_pickle(D / f"tranches_{size}.pkl"); c = R.cells(d)
        print(f"\nSIZE ${size}:")
        print(c[["cell", "split", "n", "mean_e", "t_wk", "mw_excess"]].assign(
            mean_e=lambda x: (x.mean_e * 100).round(2), mw_excess=lambda x: (x.mw_excess * 100).round(2),
            t_wk=lambda x: x.t_wk.round(2)).to_string(index=False))

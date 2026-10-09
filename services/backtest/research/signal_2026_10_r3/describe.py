"""Round 3 descriptives: gross vs cost, funding persistence, basis/squeeze risk, BY q, month signs.
usage: python3 describe.py <r3 work dir> [<h1check dir> for the H1 persistence comparison]"""
import math, sys
from pathlib import Path
import numpy as np
import pandas as pd

W = Path(sys.argv[1])
M = 38 + 29 + 18
CM = sum(1 / i for i in range(1, M + 1))
pd.set_option("display.width", 250)

for sp in ("train", "holdout"):
    e = pd.read_pickle(W / f"eps_{sp}.pkl"); e = e[e.cost500.notna()]
    cells = pd.read_csv(W / f"cells_{sp}.csv")
    # BY q over the programme: prior p = 1 except round 1 H1 (~0); our 18 cells' one-sided week-clustered p.
    p = np.sort(np.r_[cells.p_wk.values, [1e-7], np.ones(M - 19)])
    q_sorted = np.minimum.accumulate((p * M * CM / np.arange(1, M + 1))[::-1])[::-1].clip(max=1)
    qmap = dict(zip(p, q_sorted))
    g = e.groupby("cell")
    nset_exp = e.rate0 * e.nset  # naive: entry rate held for every settlement of the hold
    e = e.assign(gross=e.fund + e.hedge, fees_only=e.fund + e.hedge - 31, exp=nset_exp,
                 neg_fund=e.fund < 0, run20=e.runup > 2000, run50=e.runup > 5000, month=e.S.dt.strftime("%Y-%m"))
    g = e.groupby("cell", sort=False)
    out = pd.DataFrame({
        "n": g.size(), "gross": g.gross.mean().round(1), "gross_med": g.gross.median().round(1),
        "fees_only_net": g.fees_only.mean().round(1), "fund_med": g.fund.median().round(1),
        "exp_med": g.exp.median().round(1), "real/exp_med": (g.fund.median() / g.exp.median()).round(2),
        "fund<0": g.neg_fund.mean().round(2), "basis_entry_med": g.basis_entry.median().round(1),
        "worst_hedge_p10": g.worst_hedge.quantile(.1).round(0), "worst_mark_p10": g.worst_mark.quantile(.1).round(0),
        "worst_mark_p01": g.worst_mark.quantile(.01).round(0), "runup_p90": g.runup.quantile(.9).round(0),
        "run>20%": g.run20.mean().round(3), "run>50%": g.run50.mean().round(3),
        "months+": g.apply(lambda x: f"{(x.groupby('month').net.mean() > 0).sum()}/{x.month.nunique()}"),
        "q_BY": cells.set_index("cell").p_wk.map(lambda v: round(float(qmap[v]), 3)),
    })
    print(f"== {sp}  (m={M}, c(m)={CM:.2f})"); print(out.to_string())
    x = e[e.cell == "X0.08_h48"]
    print("X0.08_h48 corr(entry basis, hedge P&L)", round(x[["basis_entry", "hedge"]].corr().iloc[0, 1], 2),
          "| hedge mean when entry basis > +50 bps:", round(x[x.basis_entry > 50].hedge.mean(), 1), "n", int((x.basis_entry > 50).sum()),
          "| perp>spot at entry share", round((x.basis_entry > 0).mean(), 2),
          "| entry rate0 median bps", round(x.rate0.median(), 1), "| settlements/48h median", x.nset.median(),
          "| 1h-interval share", round((x.nset >= 40).mean(), 2))
    worst = x.nsmallest(5, "fund")[["sym", "S", "rate0", "fund", "hedge", "rp", "runup"]]
    print(worst.round(0).to_string())
if len(sys.argv) > 2:
    y = pd.read_pickle(Path(sys.argv[2]) / "eps_y.pkl")
    for sp in ("train", "holdout"):
        z = y[y.split == sp]
        print(f"H1 {sp}: realised funding median {z.fund1.median():.1f}, fund<0 share {(z.fund1 < 0).mean():.2f}, "
              f"perp return median {z.rp0.median():.0f}, entry basis median {z.basis_entry_px.median():.1f}")

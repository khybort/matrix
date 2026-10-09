"""Round 4 record views (not decisions): money-weighted excess, contract-clustered t, sizes, by half-year, today's regime,
time in market, risk (adverse MTM, liquidation, top-up). Usage: python3 record.py <data_dir>"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
D = Path(sys.argv[1]); sys.argv = sys.argv[:2]
import research as R  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_rows", 400)
GROUPS = ["LIN_BTC", "LIN_ETH", "INVB_BTC", "INVB_ETH", "INVB_ALT", "INVD_BTC", "INVD_ETH"]


def mw(s):
    cd = (s.capital * s.hold_days).sum()
    return (s.pnl.sum() / cd * 365 - (s.rf * s.capital * s.hold_days).sum() / cd) * 100 if len(s) else np.nan


def summary(df, split, groups=GROUPS, ys=R.YS, exits=("HOLD",), Ls=(0, 1, 2, 3)):
    a, b = R.TRAIN if split == "train" else R.HOLD
    out = []
    for g in groups:
        for Y in ys:
            for ex in exits:
                for L in Ls:
                    s = df[(df.group == g) & (df.excluded == "") & (df.b_net >= Y) & (df.exit == ex) & (df.L == L)
                           & (df.entry >= a) & (df.entry < b)]
                    if not len(s):
                        continue
                    t, _, G = R.ct(s.e, s.week); tc, _, Gc = R.ct(s.e, s.symbol)
                    out.append(dict(cell=f"{g}.Y{int(Y*100)}.{ex}.L{L}", n=len(s), mean_e=s.e.mean() * 100, t_wk=t,
                                    t_ctr=tc, ctr=Gc, mw_ex=mw(s), ann=s.ann.mean() * 100, rf=s.rf.mean() * 100,
                                    e5=s.e5.mean() * 100, hold=s.hold_days.mean(), liq=s.liq.mean() * 100,
                                    adv_p90=s.adverse.quantile(.9) * 100, adv_max=s.adverse.max() * 100,
                                    topup_p90=s.topup.quantile(.9) * 100, topup_max=s.topup.max() * 100))
    return pd.DataFrame(out).round(2)


df5 = pd.read_pickle(D / "tranches_5000.pkl")
for split in ("train", "holdout"):
    print(f"\n==== {split}, $5k, all exits/leverage")
    print(summary(df5, split, exits=("HOLD", "EARLY")).to_string(index=False))

print("\n==== sizes (holdout, HOLD): mean_e / mw_ex at $500, $5k, $50k")
rows = []
for size in R.SIZES:
    d = pd.read_pickle(D / f"tranches_{size}.pkl")
    s = summary(d, "holdout", Ls=(0, 1, 2))
    s["size"] = size
    rows.append(s[["cell", "size", "n", "mean_e", "t_wk", "t_ctr", "mw_ex"]])
print(pd.concat(rows).pivot_table(index="cell", columns="size", values=["mean_e", "mw_ex", "t_ctr"]).round(2).to_string())

print("\n==== holdout by half-year, HOLD, INV + LIN L1/L2, Y5: n, mean ann, mean rf, mean e")
ok = df5[(df5.excluded == "") & (df5.exit == "HOLD") & (df5.b_net >= 0.05) & (df5.entry >= R.HOLD[0]) & (df5.L.isin([0, 2]))]
ok = ok.assign(h=ok.entry.dt.year.astype(str) + "H" + ((ok.entry.dt.month > 6) + 1).astype(str))
print(ok.groupby(["group", "h"]).agg(n=("e", "size"), ann=("ann", "mean"), rf=("rf", "mean"), e=("e", "mean"))
      .mul([1, 100, 100, 100]).round(2).to_string())

print("\n==== share of decision days in market (best candidate b_net >= Y), by year, HOLD rows, L0/L1")
dd = df5[(df5.exit == "HOLD") & (df5.L.isin([0, 1])) & df5.b_net.notna()]
for Y in R.YS:
    print("Y", Y, (dd.assign(y=dd.entry.dt.year, on=dd.b_net >= Y).groupby(["group", "y"]).on.mean() * 100)
          .round(0).unstack().to_string())

print("\n==== entry gross annualised basis (decision) quantiles by year, INVD_BTC / LIN_BTC best candidate")
for g in ("INVD_BTC", "LIN_BTC", "INVB_ETH"):
    x = dd[dd.group == g].drop_duplicates("entry")
    x = x.assign(y=x.entry.dt.year, ga=x.b_gross_dec * 365 / x.dte * 100)
    print(g, x.groupby("y").ga.describe()[["count", "25%", "50%", "75%"]].round(1).to_string())

print("\n==== rf (OKX USDT lending) by half-year, %/yr")
o = pd.read_pickle(D / "okx_usdt_lending.pkl").set_index("ts").lendingRate
print((o.resample("6MS").mean() * 100).round(2).to_string())

print("\n==== LIN liquidation and top-up (all tranches, HOLD, Y5), % of spot notional")
lq = df5[(df5.group.str.startswith("LIN")) & (df5.excluded == "") & (df5.exit == "HOLD") & (df5.b_net >= 0.05)]
print(lq.groupby(["group", "L"]).agg(n=("liq", "size"), liq=("liq", "mean"), adv_p50=("adverse", "median"),
      adv_p90=("adverse", lambda x: x.quantile(.9)), adv_max=("adverse", "max"),
      top_p90=("topup", lambda x: x.quantile(.9)), top_max=("topup", "max")).round(3).to_string())
liqd = lq[lq.liq]
print("liquidated tranches by year/L:", liqd.groupby([liqd.entry.dt.year, "L"]).size().to_string())
print("INV adverse MTM of the short (no liquidation possible):",
      df5[df5.group.str.startswith("INV") & (df5.excluded == "") & (df5.exit == "HOLD")].groupby("group")
      .adverse.describe(percentiles=[.5, .9, .99])[["50%", "90%", "99%", "max"]].mul(100).round(1).to_string())

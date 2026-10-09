"""Round 4 (H13) in the research ledger (docs/research/ledger.jsonl). Legacy text PREREG (17edd54), so rows are written
through Ledger.append (as harness_2026_10/backfill.py did), one step per call, ledger committed after each step:

  python3 ledger_r4.py <data_dir> register   # 66 register rows — before any return is computed
  python3 ledger_r4.py <data_dir> train      # train rows from tranches_5000.pkl (week-clustered excess return, %/yr)
  python3 ledger_r4.py <data_dir> open       # holdout_open for train passers (train rows must be committed)
  python3 ledger_r4.py <data_dir> holdout    # decision holdouts (passers) + record-only holdouts (failures)
  python3 ledger_r4.py <data_dir> final      # verdicts with q from Ledger.qvalues() over every registered test
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness_2026_10"))
import _path  # noqa: E402,F401
import pandas as pd  # noqa: E402

from matrix_shared.research.ledger import Ledger  # noqa: E402
from matrix_shared.research.protocol import SubprocessGit  # noqa: E402
from matrix_shared.research.stats import clustered_t, week_key  # noqa: E402

D, STEP = Path(sys.argv[1]), sys.argv[2]
sys.argv = sys.argv[:2]
import research as R  # noqa: E402

REPO = "docs/research/ledger.jsonl"
L = Ledger(_path.ROOT / REPO, git=SubprocessGit(_path.ROOT), repo_path=REPO)
PREREG = "services/backtest/research/signal_2026_10_r4/PREREG.txt"
COMMIT = "17edd54"
GROUPS = ["LIN_BTC", "LIN_ETH", "INVB_BTC", "INVB_ETH", "INVB_ALT", "INVD_BTC", "INVD_ETH"]
DESC = {"LIN": "long Binance spot + short Binance USD-M quarterly, isolated margin N/L, capital N + N/L",
        "INVB": "long Binance spot as collateral + short Binance COIN-M quarterly (C = N F0/S0), capital N",
        "INVD": "long Binance spot as collateral + short Deribit inverse quarterly (C = N F0/S0), capital N"}


def cell_ids():
    for g in GROUPS:
        for Y in R.YS:
            for ex in ("HOLD", "EARLY"):
                for L_ in (R.LS if g.startswith("LIN") else [0]):
                    yield g, Y, ex, L_, f"r4.{g}.Y{int(Y * 100)}.{ex}" + (f".L{L_}" if L_ else "")


def sel(df, g, Y, ex, L_, split):
    a, b = R.TRAIN if split == "train" else R.HOLD
    s = df[(df.group == g) & (df.excluded == "") & (df.b_net >= Y) & (df.exit == ex) & (df.L == L_)]
    s = s[(s.entry >= a) & (s.entry < b)]
    excl = df[(df.group == g) & (df.excluded != "") & (df.b_net >= Y) & (df.exit == ex) & (df.entry >= a) & (df.entry < b)]
    nobook = df[(df.group == g) & (df.excluded == "") & df.b_net.isna() & (df.exit == ex) & (df.L == L_)
                & (df.entry >= a) & (df.entry < b)]  # alts with no live COIN-M quarterly today: no book to price
    return s, len(excl) + len(nobook)


def row(s, excl, cid, tag):
    c = clustered_t(list(s.e * 100), [week_key(t.to_pydatetime()) for t in s.entry]).as_dict()
    oth = clustered_t(list(s.e * 100), list(s.symbol))
    mw = ((s.pnl.sum() / (s.capital * s.hold_days).sum() * 365
           - (s.rf * s.capital * s.hold_days).sum() / (s.capital * s.hold_days).sum()) * 100) if len(s) else None
    return {**c, "unit": "excess annualised return on total capital, %/yr (vs OKX USDT lending)",
            "t_other": round(oth.t, 3) if oth.t == oth.t else None, "t_other_kind": "clustered by contract",
            "excluded": excl, "mw_excess_pct": None if mw is None else round(mw, 3),
            "mean_ann_pct": round(s.ann.mean() * 100, 3) if len(s) else None,
            "mean_rf_pct": round(s.rf.mean() * 100, 3) if len(s) else None,
            "liq_share": round(float(s.liq.mean()), 4) if len(s) else None}


if STEP == "register":
    rows = []
    for g, Y, ex, L_, cid in cell_ids():
        params = {"structure": DESC[g.split("_")[0]], "underlying": g.split("_", 1)[1],
                  "entry": f"daily 08:00 UTC decision, best quarterly with 14<=DTE<=200, net annualised basis >= {Y:.2f}"
                           " (4 taker fees + walks at $5k), execute at 09:00 closes",
                  "exit": "hold to delivery (spot TWAP vs delivery price)" if ex == "HOLD" else
                          "early when remaining gross annualised basis <= 2 %/yr, else delivery",
                  "leverage": L_ or "coin-margined 1x (spot is collateral)", "size_usd_per_leg": 5000,
                  "metric": "excess annualised return on total capital vs OKX USDT lending rate"}
        rows.append({"kind": "register", "test_id": cid, "round": "r4", "family": "H13",
                     "title": f"dated-futures basis {cid[3:]}", "params": params, "prereg": PREREG,
                     "prereg_commit": COMMIT, "prereg_alone": True,
                     "prereg_note": "legacy text PREREG; registered in the ledger before any return was computed",
                     "train_window": ["2021-01-01", "2024-07-01"], "holdout_window": ["2024-07-01", "2026-10-09"],
                     "cluster": "week", "min_t": 2.0, "source": "signal_2026_10_r4/ledger_r4.py"})
    L.append(rows)
    print("registered", len(rows), "m", L.m)
    sys.exit()

df = pd.read_pickle(D / f"tranches_{R.PRIMARY}.pkl")
tests = L.tests()
if STEP == "train":
    rows = []
    for g, Y, ex, L_, cid in cell_ids():
        s, excl = sel(df, g, Y, ex, L_, "train")
        r = row(s, excl, cid, "train")
        r["passed"] = bool(r["mean"] is not None and r["mean"] > 0 and r["t"] is not None and r["t"] >= 2)
        rows.append({"kind": "train", "test_id": cid, "split": "train", "source": "signal_2026_10_r4/ledger_r4.py", **r})
    L.append(rows)
    for r in rows:
        print(r["test_id"], r["n"], r["mean"], r["t"], r["passed"])
elif STEP == "open":
    L.append([{"kind": "holdout_open", "test_id": t, "source": "signal_2026_10_r4/ledger_r4.py"}
              for t, st in tests.items() if t.startswith("r4.") and st.train and st.train["passed"]])
elif STEP == "holdout":
    rows = []
    for g, Y, ex, L_, cid in cell_ids():
        st = tests[cid]
        s, excl = sel(df, g, Y, ex, L_, "holdout")
        r = row(s, excl, cid, "holdout")
        if st.train["passed"]:
            r["passed"] = bool(r["mean"] is not None and r["mean"] > 0 and r["t"] is not None and r["t"] >= 2)
            rows.append({"kind": "holdout", "test_id": cid, "split": "holdout", "record_only": False,
                         "source": "signal_2026_10_r4/ledger_r4.py", **r})
        else:
            rows.append({"kind": "holdout", "test_id": cid, "split": "holdout", "record_only": True, "passed": False,
                         "source": "signal_2026_10_r4/ledger_r4.py", **r})
    L.append(rows)
    for r in rows:
        print(r["test_id"], r["record_only"], r["n"], r["mean"], r["t"], r["passed"])
elif STEP == "final":
    q, m = L.qvalues(), L.m
    fin = []
    for t, st in tests.items():
        if not t.startswith("r4."):
            continue
        if not st.train["passed"]:
            v = "rejected_train"
        elif st.holdout["passed"] and q[t] <= 0.05:
            v = "survives"
        else:
            v = "rejected_holdout"
        fin.append({"kind": "final", "test_id": t, "verdict": v, "q": q[t], "m": m, "p_family": st.family_p,
                    "source": "signal_2026_10_r4/ledger_r4.py"})
    L.append(fin)
    L.verify()
    for f in fin:
        print(f["test_id"], f["verdict"], f["q"])
    print("m", m)

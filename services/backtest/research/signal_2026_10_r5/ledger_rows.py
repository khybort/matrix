"""Round 5 -> docs/research/ledger.jsonl (rules: PREREG.txt, 1b2b6e1; statistics: research.py).

usage: python3 ledger_rows.py <data dir> register   # 14 register rows (before any return)
       python3 ledger_rows.py <data dir> train      # from train_pass.json
       python3 ledger_rows.py <data dir> holdout    # holdout_open + holdout for train passers (from holdout.json)
       python3 ledger_rows.py <data dir> final      # record-only holdouts of train failures (cells_all.csv) + finals
Round 5 predates the harness Spec format, so rows are written like harness_2026_10/backfill.py, source "r5-script".
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness_2026_10"))
import _path  # noqa: E402,F401

import pandas as pd  # noqa: E402

from matrix_shared.research.ledger import Ledger  # noqa: E402

D = Path(sys.argv[1])
MODE = sys.argv[2]
L = Ledger(_path.ROOT / "docs/research/ledger.jsonl")
PRE = "services/backtest/research/signal_2026_10_r5/PREREG.txt"
COMMIT = "1b2b6e1591071b0ecc49d1a3e1a716dc62db7b94"
SRC = "r5-script"
DESC = {
    "A.hi": ("pct(VRP = DVOL - RV30) >= 0.90 -> long", "VRP"),
    "A.lo": ("pct(VRP) <= 0.10 -> short", "VRP"),
    "B.put": ("pct(RR25) <= 0.10 (puts rich) -> long", "RR25"),
    "B.call": ("pct(RR25) >= 0.90 (calls rich) -> short", "RR25"),
    "C": ("pct(24h DVOL log change) >= 0.95 -> fade the 24h perp move", "DVOLchg"),
    "D": ("pct(front ATM IV - back ATM IV) >= 0.90 -> long", "TERM"),
    "P": ("pct(3-day put/call notional) >= 0.90 -> long", "PC3"),
}
CELLS = ["A.hi.3", "A.hi.7", "A.lo.3", "A.lo.7", "B.put.3", "B.put.7", "B.call.3", "B.call.7",
         "C.3", "C.7", "D.3", "D.7", "P.3", "P.7"]


def tid(c):
    return f"r5.{c}"


def clean(x):
    return None if x is None or x != x else x


def stat(r):
    r = {k: clean(v) for k, v in r.items()}
    return {"n": int(r["n"]), "mean": r["net"], "median": r["median"], "t": r["t_wk"], "clusters": int(r["G"]),
            "p": r["p"], "gross": r["gross"], "funding": r["funding"]}


if MODE == "register":
    rows = []
    for c in CELLS:
        base, hold = c.rsplit(".", 1)
        title, feat = DESC[base]
        rows.append({"kind": "register", "test_id": tid(c), "round": "r5", "family": "H14", "title": f"{title}, hold {hold} d",
                     "params": {"feature": feat, "rule": title, "hold_h": int(hold) * 24, "assets": "BTCUSDT+ETHUSDT Bybit perp",
                                "decision": "00:00 UTC daily, entry close of bar ending +1h", "cost": "2 x 5.5 bps + walked spread $5k + funding"},
                     "prereg": PRE, "prereg_commit": COMMIT, "prereg_alone": True, "train_window": ["2021-03-24", "2024-07-01"],
                     "holdout_window": ["2024-07-01", "2026-10-09"], "cluster": "week", "min_t": 2.0, "source": SRC})
    L.append(rows)
elif MODE == "train":
    t = json.loads((D / "train_pass.json").read_text())["table"]
    L.append([{"kind": "train", "test_id": tid(r["cell"]), "split": "train", "source": SRC, **stat(r),
               "passed": bool(r["net"] > 0 and (r["t_wk"] or 0) >= 2)} for r in t])
elif MODE == "holdout":
    passers = json.loads((D / "train_pass.json").read_text())["passers"]
    L.append([{"kind": "holdout_open", "test_id": tid(c), "source": SRC} for c in passers])
    h = {r["cell"]: r for r in json.loads((D / "holdout.json").read_text())}
    L.append([{"kind": "holdout", "test_id": tid(c), "split": "holdout", "source": SRC, **stat(h[c]),
               "passed": bool(h[c]["net"] > 0 and (h[c]["t_wk"] or 0) >= 2)} for c in passers])
elif MODE == "final":
    passers = json.loads((D / "train_pass.json").read_text())["passers"]
    a = pd.read_csv(D / "cells_all.csv")
    a = a[a.split == "holdout"].set_index("cell")
    L.append([{"kind": "holdout", "test_id": tid(c), "split": "holdout", "record_only": True, "source": SRC,
               **stat(a.loc[c].to_dict()), "passed": False} for c in CELLS if c not in passers])
    q = L.qvalues()
    ts = L.tests()
    fin = []
    for c in CELLS:
        st = ts[tid(c)]
        if c not in passers:
            v = "rejected_train"
        elif st.holdout["passed"] and q[tid(c)] <= 0.05 and st.holdout["mean"] >= 15:
            v = "survives"
        else:
            v = "rejected_holdout"
        fin.append({"kind": "final", "test_id": tid(c), "source": SRC, "verdict": v, "m": L.m, "p_family": st.family_p,
                    "q": q[tid(c)]})
    L.append(fin)
    print("m =", L.m)
    for c in CELLS:
        print(c, fin[CELLS.index(c)]["verdict"], "q =", q[tid(c)])

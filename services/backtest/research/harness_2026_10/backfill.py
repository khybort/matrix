"""Backfill docs/research/ledger.jsonl with signal research rounds 1, 2, 3 and 3b (2026-10-09).

usage: python3 backfill.py <session scratchpad dir> [ledger path]

Reads the rounds' own result files (scratchpad: sr/results/*.json, r2/episodes_*.csv,
r3/cells_*.csv, r3b/data/results_*.json) and the committed PREREG files, and writes one
register / train / [holdout_open, holdout] / [record-only holdout] / final row per test,
in the order the rounds ran. m = 38 + 29 + 18 + 6 = 91.

Statistics written:
- `t`, `p`, `clusters`: recomputed by matrix_shared.research.stats from the per-episode
  (per-period) results where they exist — round 1 week-clustered (its own decision used
  an iid t, kept as `t_reported`), round 2 day-clustered (its decision statistic),
  rounds 3/3b week-clustered as reported.
- `passed`: the round's own pre-registered decision, unchanged.
- family p for BHY (ledger.TestState.family_p): the decision holdout's p; 1.0 otherwise.
"""

import json
import sys
from pathlib import Path

import _path  # noqa: F401
import pandas as pd

from matrix_shared.research.ledger import Ledger
from matrix_shared.research.stats import clustered_t, day_key, t_sf, week_key

S = Path(sys.argv[1])
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else _path.ROOT / "docs/research/ledger.jsonl"
RES = "services/backtest/research"
L = Ledger(OUT)
rows: list[dict] = []


def stat(values, times, key):
    c = clustered_t(list(values), [key(t) for t in times])
    return {"n": c.n, "mean": round(c.mean, 2), "median": round(c.median, 2), "t": round(c.t, 3),
            "clusters": c.clusters, "p": c.p}


def reg(tid, rnd, family, title, params, prereg, commit, train_w, hold_w, cluster, at, **extra):
    rows.append({"kind": "register", "test_id": tid, "round": rnd, "family": family, "title": title,
                 "params": params, "prereg": prereg, "prereg_commit": commit, "train_window": train_w,
                 "holdout_window": hold_w, "cluster": cluster, "min_t": 2.0, "source": "backfill", "at": at, **extra})


def add(kind, tid, at, **kw):
    rows.append({"kind": kind, "test_id": tid, "at": at, "source": "backfill", **kw})


# ======================================================================= round 1
R1 = f"{RES}/signal_2026_10/PREREG.txt"
R1C = "36d84e34aa42786b618804a9bde00b1325db1f24"
W1 = (["2025-10-01", "2026-06-01"], ["2026-06-01", "2026-10-10"])
tr = json.load(open(S / "sr/results/train.json")) | json.load(open(S / "sr/results/train_H4.json"))
ho = json.load(open(S / "sr/results/holdout.json")) | json.load(open(S / "sr/results/holdout_H2a_H2b_H3_H4_H5_H6_H7.json"))
R1_TESTS = {
    "H1": ("inverse carry after a settlement <= -0.08 %, 48 h, borrowable coins, borrow x1",
           {"signal": "bybit settled funding <= -0.0008", "legs": "long perp + short spot", "hold_h": 48,
            "universe": "coins with a Bybit or Binance VIP0 margin borrow rate today", "fees_bps": 30, "borrow": "today's VIP0 x1"}),
    "H2a": ("funding cross-section L/S deciles, 24 h", {"rank": "last settled funding /8h", "hold_h": 24, "cost_bps_rt": 15}),
    "H2b": ("funding cross-section L/S deciles, 72 h", {"rank": "last settled funding /8h", "hold_h": 72, "cost_bps_rt": 15}),
    "H3": ("premium-index cross-section, 24 h", {"rank": "24h mean premium", "hold_h": 24, "cost_bps_rt": 15}),
    "H4": ("OI-conditioned 24 h reversal", {"rank": "top quintile 24h log-OI change, reversal within", "hold_h": 24}),
    "H5": ("24 h cross-sectional reversal", {"rank": "24h return", "hold_h": 24, "cost_bps_rt": 15}),
    "H6": ("7 d momentum (skip 1 d), weekly", {"rank": "return t-8d..t-1d", "hold_h": 168, "cost_bps_rt": 15}),
}
for i in range(7):
    R1_TESTS[f"H7.dow{i}"] = (f"day-of-week {i} on the top-20 basket", {"kind": "dow", "dow": i, "side": "sign of train mean"})
for h in range(24):
    R1_TESTS[f"H7.hod{h:02d}"] = (f"hour-of-day {h:02d} on the top-20 basket", {"kind": "hod", "hour": h, "side": "sign of train mean"})

for tid in R1_TESTS:
    title, params = R1_TESTS[tid]
    reg(f"r1.{tid}", "r1", tid.split(".")[0], title, params, R1, R1C, *W1, "week", "2026-10-09T12:40:00Z",
        prereg_alone=False, prereg_note="PREREG.txt (timestamped 12:40 UTC, before any return) was committed together "
        "with the results in 36d84e3; 'written before' rests on the in-file timestamp. Rounds 2+ committed it alone.")


def r1_series(d, tid):
    per = d[tid]["periods"]
    if tid == "H1":
        per = [p for p in per if p["exec"]]
    return [p["net"] for p in per], [pd.Timestamp(p["T"]).to_pydatetime() for p in per]


for tid in R1_TESTS:
    v, t = r1_series(tr, tid)
    s = stat(v, t, week_key)
    rep = tr[tid]["exec_net"] if tid == "H1" else tr[tid]["net"]
    passed = tid == "H1"
    add("train", f"r1.{tid}", "2026-10-09T13:55:00Z", split="train", **s, t_reported=rep["t"], t_reported_kind="iid over periods",
        mean_reported=rep["mean"], passed=passed, excluded=0)
add("holdout_open", "r1.H1", "2026-10-09T13:55:00Z")
v, t = r1_series(ho, "H1")
s = stat(v, t, week_key)
add("holdout", "r1.H1", "2026-10-09T13:58:00Z", split="holdout", record_only=False, **s, t_reported=ho["H1"]["exec_net"]["t"],
    t_reported_kind="iid over episodes", t_day=round(clustered_t(v, [day_key(x) for x in t]).t, 3), passed=True)
for tid in R1_TESTS:
    if tid == "H1":
        continue
    v, t = r1_series(ho, tid)
    s = stat(v, t, week_key)
    add("holdout", f"r1.{tid}", "2026-10-09T14:00:00Z", split="holdout", record_only=True, **s,
        t_reported=ho[tid]["net"]["t"], passed=False)
add("note", "r1.H1", "2026-10-09T16:17:00Z", text=(
    "Adversarial check (wiki signal-research-2026-10 'Adversarial check'): accounting right; at $500/leg walked books "
    "and borrow x3 the net is +18.1 (t_wk 2.0) train / +23.5 (t_wk 1.5) holdout, ~0 at $5k/leg; verdict (b) real but "
    "much smaller, not sized. The ledger's 'survives' is the pre-registered x1-borrow, 30-bps test only."))

# ======================================================================= round 2
R2 = f"{RES}/signal_2026_10_r2/PREREG.txt"
R2C = "cdb5b8b40a4900490f894bc86f8eebcf90f3a183"
W2T = (["2026-05-01", "2026-08-01"], ["2026-08-01", "2026-10-09"])
W2K = (["2025-10-01", "2026-06-01"], ["2026-06-01", "2026-10-09"])
et = pd.read_csv(S / "r2/episodes_ticks_train.csv")
ek = pd.read_csv(S / "r2/episodes_klines_train.csv")
ht = pd.read_csv(S / "r2/episodes_ticks_holdout_all.csv")
hk = pd.read_csv(S / "r2/episodes_klines_holdout.csv")
h3 = pd.read_csv(S / "r2/episodes_ticks_holdout_k3.csv")
ee = pd.read_csv(S / "r2/episodes_klines_early.csv")


def times(df):
    col = "t" if "t" in df.columns else "entry"
    unit = "s" if col == "t" else "ms"
    return [x.to_pydatetime() for x in pd.to_datetime(df[col], unit=unit, utc=True)]


R2_FAMILY = {"H8a": "H8a", "H8b": "H8b", "H9": "H9", "H10": "H10", "H11": "H11", "H12": "H12"}
cells2 = sorted(set(et.cell)) + sorted(set(ek.cell))
assert len(cells2) == 28, len(cells2)
for c in cells2:
    tick = c in set(et.cell)
    reg(f"r2.{c}", "r2", c.split("-")[0], c, {"cell": c, "data": "bybit ticks (1m bars)" if tick else "1h klines/5m windows",
        "definition": f"{R2} HYPOTHESES"}, R2, R2C, *(W2T if tick else W2K), "day", "2026-10-09T13:40:00Z", prereg_alone=True)
reg("r2.H11c", "r2", "H11", "H11-flush-4h frozen, confirmation on 2024-10-01..2025-09-30 (unseen)",
    {"cell": "H11-flush-4h", "period": "2024-10-01..2025-09-30", "definition": f"{R2} ADDENDUM 14:09 UTC"},
    R2, R2C, ["2024-10-01", "2025-10-01"], ["2024-10-01", "2025-10-01"], "day", "2026-10-09T14:09:00Z",
    prereg_alone=False, prereg_note="Addendum appended to the round-2 PREREG after the train/holdout verdicts and "
    "committed with the results in 07008f1; written before its data was fetched.")
passers = {"H8b-5m-30", "H8b-5m-60", "H8b-5m-240"}
for c in cells2:
    df = et[et.cell == c] if c in set(et.cell) else ek[ek.cell == c]
    s = stat(df.net, times(df), day_key)
    add("train", f"r2.{c}", "2026-10-09T14:08:00Z", split="train", **s, passed=c in passers, excluded=0)
for c in sorted(passers):
    add("holdout_open", f"r2.{c}", "2026-10-09T14:08:00Z")
    df = h3[h3.cell == c]
    add("holdout", f"r2.{c}", "2026-10-09T14:08:30Z", split="holdout", record_only=False, **stat(df.net, times(df), day_key),
        passed=False, excluded=0)
for c in cells2:
    if c in passers:
        continue
    df = ht[ht.cell == c] if c in set(ht.cell) else hk[hk.cell == c]
    add("holdout", f"r2.{c}", "2026-10-09T14:09:00Z", split="holdout", record_only=True, **stat(df.net, times(df), day_key),
        passed=False)
# H11c: selected post hoc from a train failure (t_day 1.84) and sent straight to one confirmation test.
pf = ek[ek.cell == "H11-flush-4h"]
add("train", "r2.H11c", "2026-10-09T14:09:00Z", split="train", **stat(pf.net, times(pf), day_key), passed=True,
    gate="post-hoc selection of r2.H11-flush-4h (failed train, t_day < 2); this row is the parent's train, the "
    "confirmation below is the only decision")
add("holdout_open", "r2.H11c", "2026-10-09T14:09:00Z")
add("holdout", "r2.H11c", "2026-10-09T14:24:00Z", split="confirmation 2024-10..2025-09", record_only=False,
    **stat(ee.net, times(ee), day_key), passed=False)

# ======================================================================= round 3
R3 = f"{RES}/signal_2026_10_r3/PREREG.txt"
R3C = "8346dbd24b56e0bf849adfb3566b6b330c45b302"
c3t = pd.read_csv(S / "r3/cells_train.csv").set_index("cell")
c3h = pd.read_csv(S / "r3/cells_holdout.csv").set_index("cell")
for c in c3t.index:
    X, h = c.split("_")[0][1:], c.split("_")[1][1:]
    params = {"signal": f"bybit settled funding >= {X} %", "legs": "short perp + long spot (Bybit, else Binance)",
              "entry": "S + 1h", "hold_h": int(h), "flip_exit": c.endswith("_flip"), "fees_bps": 31.0,
              "walk_usd_per_leg": 500, "borrow": "none"}
    reg(f"r3.{c}", "r3", "H1-mirror", f"positive-funding mirror {c}", params, R3, R3C, *W1, "week",
        "2026-10-09T15:25:00Z", prereg_alone=True)


def r3row(r):
    return {"n": int(r.n), "mean": float(r.net), "median": float(r.med), "t": float(r.t_wk),
            "clusters": None, "p": float(r.p_wk), "t_other": float(r.t_day), "excluded": int(r.no_book)}


for c in c3t.index:
    add("train", f"r3.{c}", "2026-10-09T15:29:00Z", split="train", **r3row(c3t.loc[c]), passed=False,
        note="numbers from the corrected run (15:42 UTC, spot-kline window fix)")
for c in c3t.index:
    add("holdout", f"r3.{c}", "2026-10-09T15:34:00Z", split="holdout", record_only=True, **r3row(c3h.loc[c]), passed=False)

# ======================================================================= round 3b
R3B = f"{RES}/signal_2026_10_r3b/PREREG.txt"
R3BC = "e192f1045057a388c474051a196886b1e209bd58"
b_tr = json.load(open(S / "r3b/data/results_train.json"))
b_ho = json.load(open(S / "r3b/data/results_record.json"))["holdout"]
for c in b_tr:
    X, ex = c.split(".")
    params = {"signal": f"bybit settled funding <= -0.{X[1:]} %", "legs": "long Bybit perp + short hedge-venue perp",
              "hedge_venue": "least negative last settled rate /8h among Binance/OKX/Bitget/Gate (point in time)",
              "exit": {"F24": "fixed 24 h", "F48": "fixed 48 h", "CONV": "first Bybit rate above hedge's, cap 96 h"}[ex],
              "walk_usd_per_leg": 500, "liq_gate": "3x liquidation-adjusted net > 0"}
    reg(f"r3b.{c}", "r3b", "H1-perp-hedge", f"H1 with a perp-perp hedge {c}", params, R3B, R3BC, *W1, "week",
        "2026-10-09T15:35:00Z", prereg_alone=True)


def r3brow(d):
    return {"n": d["n"], "mean": d["mean"], "median": d["median"], "t": d["t_wk"], "clusters": d["G_wk"], "p": d["p_wk"],
            "t_other": d["t_day"]}


for c in b_tr:
    add("train", f"r3b.{c}", "2026-10-09T16:44:00Z", split="train", **r3brow(b_tr[c]["net"]), passed=False,
        excluded=sum(b_tr[c]["skipped"].values()))
for c in b_tr:
    add("holdout", f"r3b.{c}", "2026-10-09T16:47:00Z", split="holdout", record_only=True, **r3brow(b_ho[c]["net"]), passed=False)

# ======================================================================= finals
L.append(rows)
q, m = L.qvalues(), L.m
fin = []
for tid, st in L.tests().items():
    if not st.train["passed"]:
        verdict = "rejected_train"
    elif st.holdout["passed"] and q[tid] <= 0.05:
        verdict = "survives"
    else:
        verdict = "rejected_holdout"
    fin.append({"kind": "final", "test_id": tid, "at": "2026-10-09T17:00:00Z", "source": "backfill", "verdict": verdict,
                "q": q[tid], "m": m, "p_family": st.family_p})
L.append(fin)
L.append({"kind": "note", "at": "2026-10-09T17:00:00Z", "source": "backfill", "text": (
    "Backfill of rounds 1, 2, 3, 3b (m = 91) by services/backtest/research/harness_2026_10/backfill.py. Round 4 "
    "(17edd54, 66 cells) was pre-registered but had no verdicts at backfill time; it enters through the harness.")})
L.verify()
print("m", m, "rows", len(L.rows()))
for tid in ("r1.H1", "r2.H8b-5m-30", "r2.H8b-5m-60", "r2.H8b-5m-240", "r2.H11c"):
    st = L.tests()[tid]
    print(tid, "train", st.train["mean"], st.train["t"], "holdout", st.holdout["mean"], st.holdout["t"], st.holdout["clusters"],
          "p", st.family_p, "q", q[tid])

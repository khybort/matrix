---
title: Research harness — pre-registered hypothesis tests in minutes
updated: 2026-10-09
sources: [packages/python-shared/src/matrix_shared/research/, packages/python-shared/tests/test_research_harness.py, docs/research/ledger.jsonl, services/backtest/research/harness_2026_10/ (backfill.py, round3_rerun.py)]
status: current
---

On 2026-10-09 five rounds of signal research (91 tests, [[signal-research-2026-10]])
were run by hand-written scripts, and each script re-implemented the same protocol.
They also counted m differently (38, then 29, then "38 + 29 + 18"). `matrix_shared.research`
is that protocol as code. A study writes one function, the **builder**, which turns a cell's
rule into episodes. Everything that could be gamed belongs to the harness.

## Where it lives and why
- It lives at `packages/python-shared/src/matrix_shared/research/` because the shared
  package is the only code that both the backtest and dev_agent images import. A package under
  `services/backtest/research/` would be invisible to dev_agent.
- It is pure standard library: no numpy, pandas or DB. It imports in every image, and it imports
  on the host python (3.9, the one with pandas) through
  `services/backtest/research/harness_2026_10/_path.py`. That loader skips
  `matrix_shared/__init__.py`, which needs the DB stack. This is also why the code avoids
  3.10+ syntax (`timezone.utc`, `zip` without `strict`).
- Builders may use pandas. Data loading is the study's business.

## The ledger — `docs/research/ledger.jsonl`
- It holds one JSON row per event: `register`, `train`, `holdout_open`, `holdout`
  (decision, or `record_only` for a train failure), `final` and `note`.
- Each row carries `seq` and `prev`, the sha256 of the previous line. `Ledger.verify()` walks
  that chain. `append()` refuses a broken chain, and it refuses a file that no longer starts with
  its version at HEAD.
- **m is the number of registered tests, and q comes from `Ledger.qvalues()` and nowhere else.**
  The family p of a test is its decision-holdout one-sided p. Every other test gets p = 1, because
  a train failure never rejects its null.
- Backfilled 2026-10-09 from rounds 1, 2, 3 and 3b: **m = 91**, 371 rows. Statistics were
  recomputed from each round's per-episode files:
  - round 1: week-clustered; its own iid t is kept as `t_reported`;
  - round 2: day-clustered, its own decision statistic;
  - rounds 3 and 3b: week-clustered, as reported.

  Every recomputed figure matches the wiki. For example, H1 train +104.2 at t_wk 10.73, holdout
  +135.0 at t_wk 8.14 over 19 weeks, and H8b-5m-60 holdout +18.1 at t_day 1.05.
- **Current q (m = 91):** `r1.H1` p = 9.6e-8, **q = 4.4e-5**: the only test with q ≤ 0.05.
  - The three H8b-5m decision holdouts have q = 1, and so does H11c's confirmation.
  - Round 4 (dated-futures basis, `17edd54`, 66 cells) was pre-registered but had no verdicts at
    backfill time. It enters through the harness, and m then becomes 157. H1's q at m = 157 is
    about 8.5e-5.
  - H1's "survives" is the pre-registered test at borrow ×1 and 30 bps. The adversarial check
    puts the realistic edge near +20 bps at t ≈ 1.5–2, and a `note` row on `r1.H1` records this.
- Round 1's PREREG was committed together with its results (36d84e3). The register rows say so
  (`prereg_alone: false`), as do H11c's (an addendum committed in 07008f1).

## How to run a study
```python
from matrix_shared.research import Cell, Episode, Spec, register

spec = Spec(round="r5", family="H14", title="...", question="...",
            cells=(Cell("X08_h48", {"x": -0.0008, "hold_h": 48}), ...),
            train=("2025-10-01", "2026-06-01"), holdout=("2026-06-01", "2026-10-10"),
            cluster="week", min_t=2.0, q_max=0.05, min_entry_lag_s=3600,
            data="...", costs="...", survivorship="...")

def builder(cell, start, end):          # every signal with info_time in [start, end)
    ...
    yield Episode(sym, info_time, entry_time, exit_time, net_bps, {"fund": f, "cost": c})

study = register(spec, "services/backtest/research/signal_2026_11/PREREG.md")
```
1. **Register.** The first `register(...)` call writes the PREREG (rendered text plus a
   `harness-spec` JSON block) and raises `NotCommitted`. Commit that file **alone**
   (`git commit -m "docs: pre-register r5" -- <file>`), then call `register` again. That second
   call writes one `register` row per cell.
2. **Train.** `study.evaluate(builder)` returns one `CellResult` per cell, with n, excluded,
   mean, median, clustered t and p, `passed`, and t under the other clustering. Each train row is
   appended as soon as its cell is reached. Then commit the ledger.
3. **Holdout.** `study.open_holdout(cell_id, builder)` runs for train passers only. It needs the
   train verdict committed, and it writes `holdout_open` before computing anything.
4. **Record.** `study.record_holdout(cell_id, builder)` works only after every train verdict is
   in, and only for failures. Its rows never enter q.
5. **Finalise.** `study.finalise()` writes the verdicts:
   - `rejected_train`;
   - `rejected_holdout`;
   - `survives`, which needs the holdout gate **and** cumulative q ≤ `q_max` over the whole
     ledger.

   Commit the ledger.

Building blocks:
- `research.funding.funding_bps`: every raw settlement in (entry, exit] counted once, notional
  marked to market, long pays a positive rate.
- `research.costs`: `walk_bps`, `round_trip_bps`, `hedged_cost_bps`, VIP0 `TAKER_BPS`. This is
  the same walk as `backtest.carry_books.walk_bps`. Its owner should switch that import here; the
  behaviour is identical.
- `research.fetch.PoliteFetcher` plus `bybit_klines`, `binance_spot_klines`, `bybit_funding`
  and the book fetchers:
  - a per-host minimum interval;
  - `Retry-After` honoured on 429;
  - Binance `X-MBX-USED-WEIGHT-1M` read on every response;
  - **418 blocks the host for the rest of the process**;
  - an on-disk cache for history;
  - kline and funding windows **clipped to [start, end]**. This is round 3's Bybit delisted-pair
    bug, fixed once.
- `research.episodes.collapse_signals`: the live book's signals, collapsed through
  `edge_study.one_per_episode`, so the engine and research share one episode definition.

## Forward tests (added 2026-10-09, first use r5f)
A rule that was already seen in history (and so cannot get an honest holdout) is re-tested on data that did
not exist when it was registered: `Spec(forward_n=60, train=<in-sample window, cited only>,
holdout=<forward window>)`. `evaluate()` is refused; `study.forward_status(cell, builder)` returns only how many
of the first `forward_n` episodes (by signal time) have closed; `study.open_forward(cell, builder)` is refused
until all of them have, then writes `holdout_open`, decides on exactly those `forward_n` (same t gate) and
`finalise()` gives `survives` (also q ≤ `q_max` at that moment) or `rejected_forward`. An episode still open
holds its place even if the builder cannot price it yet. The ledger refuses a train row, a second open and an
early final for such a test. `forward_n` appears in the spec block only when > 0, so earlier committed specs
still match. Example: `services/backtest/research/signal_2026_10_r5f/forward.py`.

## Guards (each has a test in `test_research_harness.py`)
| guard | raises |
|---|---|
| PREREG not committed, committed with other files, or its spec block differs from the run (or was edited later) | `NotCommitted` / `SpecMismatch` |
| cell id re-registered with different params, or a mix of new and existing cells | `FrozenCellError` |
| same spec again → *replicate*: recomputes, never writes, cannot open a holdout the original never opened | `HoldoutError` |
| second train evaluation of a cell | `FrozenCellError` |
| holdout before train verdict, before it is **committed**, for a train failure, or twice (a crash still spends it) | `HoldoutError` |
| record-only holdout of a passer | `HoldoutError` |
| entry not strictly after `info_time + min_entry_lag_s`, exit before entry | `LookaheadError` |
| signal outside the split's window (e.g. holdout data in train) | `WindowError` |
| edited, removed or reordered ledger row; rewritten committed history | `LedgerError` |
| out-of-order ledger rows written directly (train before register, holdout result without `holdout_open`) | `LedgerError` |

One sample per episode is enforced on the builder's output. Per symbol, a signal before the
previous kept exit is dropped. An unpriced episode (no book, NaN net) still occupies its symbol,
as in round 3, and is then excluded and counted.

What it cannot stop:
- a builder that reads future data inside its own code (the window check sees only `info_time`);
- uncommitted ledger rows being truncated before the next commit.

Commit the ledger after every step; the holdout guard enforces this for train verdicts.

## Proof: round 3 re-run (2026-10-09)
`services/backtest/research/harness_2026_10/round3_rerun.py` re-ran round 3 through the
harness:
- legacy PREREG `8346dbd`, as a replicate against a copy of the ledger;
- cached data in the session scratchpad: `sr/data`, `r3/spot_1h.pkl` and `r3/books.pkl`.

All 18 cells × train and record holdout (36 rows) match `cells_*.csv`:
- n and excluded counts exactly;
- net within 0.05 bps;
- t_wk and t_day within 0.005.

`open_holdout` on a train failure is refused. The re-run takes 14 s on the host. The only
cell-specific code is the ~40-line builder.

## For the dev_agent
To test a hypothesis, write a builder and a `Spec`, then follow the five steps. Never hand-roll
splits, t or BHY. Never edit `ledger.jsonl` by hand. A changed rule is a new cell id: m grows,
and that is the cost of looking. `dev_agent_lessons` carries this as a lesson.

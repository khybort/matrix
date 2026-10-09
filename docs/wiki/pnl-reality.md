---
title: PnL reality — why it loses money
updated: 2026-10-09
sources: ["db: outcomes ⋈ predictions, 30d window to 2026-09-19", "packages/python-shared/src/matrix_shared/trading.py", "db: wallets"]
status: current
---

The honest state of the only thing that matters. Measured, not modelled.

## Claims
- **Wallets (2026-09-19, paper):**

| wallet | equity | vs start |
|---|---|---|
| crypto / default | 9 513.31 | −486.69 |
| crypto / shadow | 9 892.62 | −107.38 |
| bist / default | 9 913.42 | −86.58 |
| us / default | 9 999.18 | −0.82 |

  The crypto figure is after the **−346.76 refund applied 2026-09-19**: that
  amount was a bug, not a strategy — challenger positions booked into the
  champion wallet with no slot cap (2026-09-13, fixed in 74eb6ff).

- **Trade economics, 30 days to 2026-09-19** (champion wallets, orphan closes
  excluded, n = 2 886): **net −161.73 USD, mean −18.2 bps per trade.**

| exit | n | mean bps |
|---|---|---|
| hit_tp | 425 | +160.3 |
| hit_sl | 788 | −129.4 |
| hit_horizon | 1 651 | −11.3 |

- **The structural problem is hit rate, not payoff.** Payoff ratio is
  160.3/129.4 = **1.24 : 1**; with that ratio breakeven needs a **44.7 %** win
  share among TP/SL exits. Actual is 425/1213 = **35.0 %**. That ~10-point gap
  is the bleed. No parameter tuning closes a gap that size — it is a signal
  problem.
- **Horizon exits are pure cost.** 1 651 trades (57 % of all exits) end at the
  horizon averaging −11.3 bps, against a modelled round-trip cost of ~15 bps
  (crypto taker 5.5 + slippage 2, per side; `trading.execution_cost_bps`).
  Gross drift on those trades is ≈ 0: the system is paying the spread to learn
  nothing.
- **~~Part of the loss was self-inflicted~~ — withdrawn 2026-10-09.** The
  2026-09-20 claim that `momentum_xs`'s *signals* beat random entry by +36.0
  bps (t=6.04, n=799) while its fills lost −35.8 was pseudo-replication: 87 %
  of those rows were one three-hour burst re-emitting the same ten bets
  ([[edge-study]]). Counted once per bet, with no look-ahead and no scoring
  across bar gaps, the signals measure **−23.2 bps vs random entry (t=−2.05,
  277 episodes from 2 188 rows, 30 d to 2026-10-09)**. The fills lost because
  the signal had nothing to give, not because they were late. Fills *were*
  20–53 % late across the book and the freshness gate in [[paper-engine]]
  still fixes that, but it was not hiding an edge.
- **`grid`, `dca` and `funding_reversion` still have no signal** (−0.7, −2.7,
  +3.0 bps vs random) and produce most of the volume — the cost engine.
- **Therefore: no strategy in the book has demonstrated edge after costs** (2026-10-09:
  on the corrected study none clears BHY at m=14; the best is `funding_reversion`
  +3.5 bps vs random time, t=1.34, against a ~15 bps round trip).
  The system is not losing because of a leak or a mis-parameterisation; it is
  losing because its signals are, so far, indistinguishable from noise once
  costs are charged. G3 in [[purpose-and-goals]] is far away.
- **Sample caveat.** These 30-day numbers are really 4 days of trading
  (2026-09-12 → 09-16); the system was dead for the rest ([[incidents]]).
  42 810 outcomes exist since 2026-06-01, but they predate the cost model and
  several correctness fixes, so they are not comparable.

## What would change the picture
- Fewer, higher-conviction trades: at 35 % hit rate the only profitable
  configuration is one where the payoff ratio exceeds 1.9 : 1, or where trades
  that would end at the horizon are never opened.
- An entry filter with measurable discrimination — the counterfactual ranker
  already reports that traded and skipped candidates perform about the same
  (traded −15.0 bps vs skipped −13.9 bps, 2026-09-13), which says the current
  ranking adds nothing.
- See [[open-questions]] for the experiments that would settle this.

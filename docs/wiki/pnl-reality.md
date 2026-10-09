---
title: PnL reality — why it loses money
updated: 2026-10-09
sources: ["db: outcomes ⋈ predictions, 30d window to 2026-09-19", "db: wallets.day_start_equity 2026-10-09 12:01 UTC", "edge study entry-rule correction cda6ee6", "packages/python-shared/src/matrix_shared/trading.py", "db: wallets"]
status: current
---

The honest state of the only thing that matters. Measured, not modelled.

## Claims
- **Wallets, 2026-10-09 day start (paper, `wallets.day_start_equity`):**
  crypto/default 9 473.91 (−526.09), crypto/shadow 9 841.14 (−158.86),
  bist/default 9 908.60 (−91.40), us/default 9 999.16 (−0.84). BIST/US
  are paused since 2026-10-09, so their figures are frozen.
- **Wallets (2026-09-19, paper), kept for history:**

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
  across bar gaps, the signals measured −23.2 bps vs random entry (t=−2.05,
  277 episodes, 30 d to 2026-10-09) under the pre-signal entry rule, and
  **−32.1 (t=−2.92, 287 episodes)** under the honest rule (entry at the first
  bar opening after the signal; cda6ee6, [[edge-study]] "Correction —
  pre-signal path"). It is significantly *worse* than random entry. The fills lost because
  the signal had nothing to give, not because they were late. Fills *were*
  20–53 % late across the book and the freshness gate in [[paper-engine]]
  still fixes that, but it was not hiding an edge.
- **`grid`, `dca` and `funding_reversion` have no tradeable signal**
  (2026-10-09, 30 d, honest entry rule, vs random time: grid +2.9 t=1.93,
  dca +1.0 t=0.51, funding_reversion +1.7 t=0.66; gross levels +3.0 / +1.1
  / +3.5 bps against a 12–15 bps round trip). They produced most of the
  volume: the cost engine. (Superseded 2026-10-09: the 09-19 figures −0.7 /
  −2.7 / +3.0 were rows-as-samples.)
- **Therefore: no strategy in the book has demonstrated edge after costs** (2026-10-09:
  on the corrected study none clears BHY at m≈14; under the honest entry
  rule the best lead over random time is grid +2.9 bps, t=1.93, at +3.0 bps
  gross against a ~15 bps round trip — cda6ee6). The only candidate is
  outside the book: `neg_funding_carry`, shadow-only, no closed episode yet
  ([[signal-research-2026-10]]).
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
  ranking adds nothing. Confirmed 2026-10-09 on 8 891 clean episodes:
  Spearman(EV, net) −0.057 ([[open-questions]]).
- See [[open-questions]] for the experiments that would settle this.

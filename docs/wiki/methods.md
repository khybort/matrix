---
title: Quant methods — what is applied, what is next
updated: 2026-10-09
sources: ["Bailey & López de Prado, The Deflated Sharpe Ratio (SSRN 2460551)", "López de Prado, Advances in Financial Machine Learning (triple barrier, meta-labeling)", packages/python-shared/src/matrix_shared/{edge_study,barrier_study,barriers}.py]
status: current
---

Established methodology this system uses, why each one was adopted (always in
response to a measured failure, never as decoration), and what is queued.

## Applied

- **Drift-controlled measurement, everywhere.** Every study compares a signal
  against what a random entry in the same symbol and period would have earned.
  Without it, a rising tape makes every long look like alpha — the first
  horizon profile showed `funding_reversion` "+232 bps at 90 minutes" purely
  from market drift.

- **Two-null permutation test** — [[edge-study]]. A strategy is compared
  against itself with (a) random entry times, same side, and (b) random side,
  same moment. Only both together separate "knows when" from "knows which way";
  judging on one null risks deleting a strategy that has the other kind of
  edge. The 2026-09-20 headline (`momentum_xs` +31.2 bps t=5.25 / +33.0 bps
  t=5.55) is **withdrawn**: it counted re-emitted rows as trades. Counted per
  bet it is −23.2 (t=−2.05) / −18.6 (t=−1.64); no strategy beats either null
  after correction (2026-10-09, 30 d).
- **One bet, one sample** (2026-10-09) — `edge_study.episode_groups`. A signal
  re-emitted for the same (strategy, market, symbol, side) inside the horizon
  of the first one is the same bet. Every study (edge, barrier, horizon,
  execution, meta-label), the paper certificate, efficacy and the slot scorer
  count episodes; fills of one episode have their dollars summed. Effect:
  funding_reversion 13 804 rows → ~1 270 episodes, 3 101 fills → 473 bets.
  FDR/BHY cannot fix this: the error is in n, not in the p-value threshold.
- **Never score across a hole in the bars** (2026-10-09). An entry whose last
  closed bar is more than 3 min old, or a window with two consecutive bars more
  than 3 min apart, is unscorable. Without it `bist_volume_breakout` read +104
  bps (t=16.7; its stale entries were all +300 bps take-profits) and is −10.7
  (t=−1.79) with it. A single-strategy run is also corrected for the whole
  family (BHY padded with p=1, DSR deflated for m), not for m=1.
- **Evaluate signals, not fills.** Only ~12 % of predictions become positions,
  and the selection is not random, so a fills-only study measures the selector,
  not the strategy. Since 2026-09-20 every prediction is replayed. This also
  lets a strategy that holds zero slots keep earning evidence for free.
- **Triple barrier with volatility-scaled levels** (López de Prado) —
  `matrix_shared/barriers.py`. Barriers are set at m·σ_h (σ_h = 1m realised
  volatility × √horizon) instead of fixed percentages, so a label means the
  same thing in every regime. Adopted after `make barrier-report` measured
  take-profits sitting 8.8σ (`grid`) and 6.8σ (`matrix_agent`) away — barriers
  no trade could reach, which is why 57 % of exits were time exits at minus the
  round trip. Replay (2026-09-20, rows-as-samples): `oi_breakout` −6.7 →
  +14.1 net bps, `momentum_xs` +15.7 → +26.9. On episodes (2026-10-09, 30 d)
  `momentum_xs` is −33.4 now and −23.4 at its best multiple; `oi_breakout`
  −22.3 → +41.3 at m=2.0 on 188 episodes — untested for significance.
- **Multiple-testing correction** (Benjamini-Hochberg at FDR 5 %) — testing 13
  strategies at once means an uncorrected 5 % threshold yields roughly one
  false discovery per run, the mechanism behind backtests that never repeat
  (Bailey & López de Prado). Applied to every edge report: on 2026-09-20 only
  **2 of 13** survived, and one of those has n=33.
- **Signal freshness gate** — [[paper-engine]]. A bracket is only the trade the
  strategy proposed if it is opened promptly; fills 20–53 % into the horizon
  were the largest measured value destroyer.
- **Cost-aware gating everywhere** — an edge is only real if it exceeds the
  round trip. Since 2026-09-20 that number is **measured per symbol**
  (`matrix_shared/symbol_costs.py`): median top-of-book spread from our own
  snapshots, halved for the crossing, floored and capped. Measured range
  1.16–5.95 bps spread, i.e. 12.0 bps round trip on BTCUSDT against 16.9 on
  BRUSDT — the old flat 15 was simultaneously too harsh for majors and too
  generous for illiquid alts, distorting the EV floor in both directions.
- **Allocate on evidence, not on trailing PnL** — [[learning-loop]]. A strategy
  that beats a null by more than its round trip gets a full wallet share even
  if its realised history is poor; `perf_score` reflects fills that may have
  been broken. `momentum_xs` went 1 → 7 slots on this rule — on the withdrawn
  edge. Since 2026-10-09 a strategy with no active config or no signal in 24 h
  is never promoted.
- **Size on the lower bound of a measured edge, not its point estimate.**
  Kelly is about an order of magnitude more sensitive to errors in the mean
  than in the variance, so a point estimate plus full Kelly is a reliable way
  to go broke while being directionally right. We take the 95% lower bound,
  quarter it, and keep the risk gate above it. See [[research-backlog]] #5.
- **Correct for having searched.** Thirteen strategies scored on the same bars
  are not thirteen independent tests, and the best of them is biased upward
  even if all are worthless. Significance is Benjamini-Yekutieli, Sharpe is
  deflated for the number of trials, and the sample size a strategy needs is
  registered before the data can argue about it. See [[research-backlog]] #7.
- **A tie is an answer** — a challenger indistinguishable from its champion
  after ≥100 outcomes each (|z| < 0.5) is retired rather than run to its
  14-day limit. One challenger slot per strategy means an inconclusive
  experiment is blocking the queue behind it.
- **Wilson lower bounds and CI-gated certificates** — [[learning-loop]],
  [[risk-gates]]: small-sample win rates are not point estimates.

## Queued
Moved to [[research-backlog]], which now carries a decisive experiment and a
pass criterion for each candidate, plus the methods judged dead ends here.

**Meta-labeling was tried and failed on this data** (2026-09-20; re-run on
episodes 2026-10-09: the one "useful" model, matrix_agent/crypto lift +107 bps at
AUC 0.59, was re-emissions straddling the split — AUC 0.52 on episodes): a secondary
model scoring each signal reached AUC 0.43–0.54 out of sample with lift
≤ +2.9 bps, so it is not wired to capital. The threshold for shipping one is
lift ≥ 5 bps at AUC ≥ 0.55.

## Open questions
- Does the meta-labeller beat "fill the freshest signal", the policy the
  freshness gate now implements by default?

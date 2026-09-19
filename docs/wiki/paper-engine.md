---
title: Paper engine
updated: 2026-09-19
sources: [services/backtest/src/backtest/paper_trade.py, packages/python-shared/src/matrix_shared/allocation.py]
status: current
---

`backtest` turns predictions into positions and positions into outcomes on a
5-second loop. It is the measurement instrument for the whole system, so its
bugs corrupt every downstream conclusion.

## Claims
- **Candidate selection**: open predictions with no position, inside their
  horizon, whose strategy version is `active` or `shadow` (retired versions are
  excluded since 2026-09-13 — their queued predictions used to keep trading),
  ordered by confidence and recency, then re-ranked by expected value
  (`allocation.expected_value`: confidence × tp − (1−confidence) × sl, scaled by
  symbol edge, strategy perf and pair edge).
- **Two slot layers**: wallet-level `max_concurrent_positions`, and per-strategy
  `allocated_slots` from `strategy_slot_configs`. The shadow pass borrows the
  champion's slot rows, so a challenger runs under the same capital discipline.
- **Sizing**: `risk_multiplier` = confidence × perf multiplier × pair edge,
  halved after 3 consecutive losses, quartered after 8. Corrected 2026-09-15 to
  map `perf_score` from its [0,1] scale into [0.25, 1.25] — before that every
  neutral strategy was sized at the 0.25× floor.
- **Costs are charged on both sides**: taker fee + slippage per side from the
  market adapter, plus funding PnL for held crypto positions.
- **Exits**: take-profit, stop-loss, horizon, funding flip, forced flatten on a
  circuit trip. `orphan_flat_close` marks positions closed for bookkeeping and
  is excluded from every metric — it is not evidence.
- **Counterfactuals**: predictions that were never opened get a
  `context.virtual_outcome` so the ranker can be measured against what it
  skipped.
- **Known-corrupting bugs, now fixed** (treat pre-fix data as unusable):
  challenger positions in the champion wallet (2026-09-13), unordered candidate
  `LIMIT`, and `momentum_xs` stamping every version as v1.

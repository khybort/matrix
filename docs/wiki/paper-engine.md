---
title: Paper engine
updated: 2026-10-09
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
- **EV floor (2026-10-09, 5164dbc)**: a candidate opens only if its EV is at
  least `MATRIX_MIN_EV_OVER_COST` × the round trip (2× for a two-leg carry);
  where the edge study has measured the strategy, the 95 % lower bound of that
  edge replaces the model's `confidence × tp` EV. The ranker itself cannot
  discriminate (Spearman −0.057, [[open-questions]]), so today the floor
  mostly blocks. One open position per (strategy, symbol, side) (1230a23):
  2 016 of funding_reversion's 3 101 fills had stacked onto an identical open
  bet.
- **Freshness gate (2026-09-20)**: a candidate more than
  `MATRIX_MAX_SIGNAL_AGE_FRAC` (20 %) into its own horizon is not opened, with
  an absolute floor of `MATRIX_MIN_SIGNAL_WINDOW_S` (45 s) so short-horizon
  signals stay fillable; EV additionally decays by the elapsed fraction so a
  stale candidate cannot outrank a fresh one. Before this, queued predictions
  competed until `close_by` and the average fill landed 20–53 % into the
  horizon — the single largest measured value destroyer in the system
  ([[edge-study]]). Refused candidates still earn a virtual outcome, so the
  evidence is kept for free.
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
  circuit trip. Book-priced carries (whose context names a spot leg) never
  flip-exit; they hold to horizon (e8bdd2f). `orphan_flat_close` marks positions closed for bookkeeping and
  is excluded from every metric — it is not evidence.
- **Counterfactuals**: predictions that were never opened get a
  `context.virtual_outcome` so the ranker can be measured against what it
  skipped.
- **Funding is booked per settlement, at the rate in force** (2026-10-09,
  `backtest.carry_funding`). Before, a carry earned `hours/8 x rate_at_close`
  and closed on the first adverse ticker reading. Bybit reports a
  `+0.0000125` placeholder for about a minute after every settlement, so every
  inverse and xexch carry "flipped" at its first settlement and booked a near
  zero accrual: 51 of 51 closed `funding_flip`, mean −27 bps, 0 % wins — the
  four-leg fee and nothing else. A flip now needs five minutes of adverse
  readings, and directional crypto trades pay funding only for settlements
  they actually cross. Carry outcomes before 2026-10-09 are not evidence.
- **Carry family** (7d7b854 onward): `delta_neutral`, `inverse_carry` and
  `xexch_carry` are booked as one family, with one carry per underlying per
  wallet. A carry that names a spot leg is gated, sized and costed on both
  books (`backtest.carry_books`): no spot book means no position; size is
  capped by book impact and by $500 a leg until the strategy is `confirmed`;
  the cost is four taker fees plus walked fills. Borrow is charged per started
  hour from the recorded `margin_borrow_rates` series, or the entry quote × 3
  where the series misses (32dcadb; `context.borrow_source`). An open carry
  is marked at settled funding minus the full round trip and accrued borrow,
  so the daily-loss circuit sees what closing would realise (69f37c8). Closes
  never wait on REST.
- **Kelly** sizes only a `confirmed` strategy, on the 95 % lower bound of its
  edge; a carry row is already net of cost, and since 3af1eee the round trip
  is not charged twice.
- **Known-corrupting bugs, now fixed** (treat pre-fix data as unusable):
  challenger positions in the champion wallet (2026-09-13), unordered candidate
  `LIMIT`, and `momentum_xs` stamping every version as v1.

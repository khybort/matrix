---
title: Open questions
updated: 2026-09-19
status: current
---

What is genuinely unknown, phrased so a future session can close it. Ranked by
how much the answer would change.

## Claims
- **Does any signal in the book have edge before costs?** The 30-day evidence
  says no ([[pnl-reality]]): horizon exits average −11.3 bps against a ~15 bps
  round trip, i.e. gross ≈ 0. *Test*: compute gross PnL (pre-fee, pre-slippage)
  per strategy over a window with enough n, and compare each strategy's
  distribution against a random-entry control on the same symbols and horizons.
  Until one strategy separates from the control, tuning is theatre.
- **Is the horizon wrong, or the entry?** 57 % of exits are horizon exits at
  roughly minus cost. *Test*: for closed trades, measure max favourable and
  adverse excursion within the horizon. If MFE frequently exceeds the TP
  distance, the problem is exit timing; if not, the entry has no predictive
  content.
- **Can the EV ranker discriminate at all?** The counterfactual report says
  traded and skipped candidates perform about the same (−15.0 vs −13.9 bps,
  2026-09-13). *Test*: rank all candidates by EV, bucket into deciles, and plot
  realised PnL per decile. A flat curve means the ranker is decoration.
- **What is the true cost model?** Fees and slippage are assumed
  (5.5 + 2 bps per side for crypto) and never validated against fills. Until
  live or exchange-simulated fills exist, every net-of-cost conclusion carries
  that assumption.
- **Is the 15-second cadence justified?** Nothing has shown that faster
  decisions earn more than they cost in spread. *Test*: compare realised PnL of
  the same strategy at 15 s vs 60 s vs 300 s decision intervals.
- **Should BIST and US be running at all right now?** They add surface area,
  cost and attention while crypto has no edge. The argument for keeping them is
  regime diversification; there is no evidence yet either way.
- **Operator-blocked**: lid sleep (`sudo pmset -a disablesleep 1`), the
  wallet refund, `TELEGRAM_BOT_TOKEN` (without it every alert is invisible —
  this is what let the three-day outage pass unnoticed).

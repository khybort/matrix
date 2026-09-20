---
title: Open questions
updated: 2026-09-19
status: current
---

What is genuinely unknown, phrased so a future session can close it. Ranked by
how much the answer would change.

## Claims
- ~~Does any signal in the book have edge before costs?~~ **Answered
  2026-09-19** by [[edge-study]]: one does (`oi_delta`, +19.4 bps vs random,
  t=2.90), two are significantly worse than random, and the two highest-volume
  strategies have gross ≈ 0. The follow-up question is now: **does `oi_delta`'s
  edge hold out of sample, and can the book be rebuilt around it?**
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
- **Operator-blocked**: only `TELEGRAM_BOT_TOKEN` remains — without it every
  alert is invisible, which is what let the three-day outage pass unnoticed.
  The wallet refund was applied 2026-09-19 and lid sleep was disabled
  2026-09-20, so the system can finally accumulate an uninterrupted sample.

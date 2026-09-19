---
title: Strategies
updated: 2026-09-19
sources: [services/strategy/src/strategy/modules/, services/agent/src/agent/decide.py, "db: strategy_configs"]
status: current
---

Two producers of predictions: 12 deterministic modules (`strategy`) and one
LLM agent (`matrix_agent`). Both write into the same `predictions` table and
are measured identically.

## Claims
- **Crypto modules**: `funding_reversion`, `grid`, `dca`, `oi_delta`,
  `oi_breakout`, `momentum_xs`, `cash_and_carry`, `screener_follow`, plus
  experimental `xexch_funding_arb` and `inverse_carry`.
  **BIST/US modules**: `gap_fade`, `intraday_reversion`, `volume_breakout`,
  `news_event`.
- **Worst contributors, 30 d to 2026-09-19**: `funding_reversion`
  (1 007 trades, −80.65 USD, −21.3 bps), `grid` (899, −27.67, −11.9),
  `momentum_xs` (204, −15.98, −40.0), `oi_delta` (278, −15.82, −13.9). Trade
  count and loss track each other almost exactly, which is the signature of a
  cost-dominated book ([[pnl-reality]]).
- **A config is `active`, `shadow` or `retired`.** `active` is the champion;
  `shadow` is a challenger running in parallel on the shadow wallet; the
  efficacy job promotes or retires it ([[learning-loop]]). A strategy with no
  active config emits nothing.
- **The agent blends rather than overrides** (`decide.blend_decisions`,
  `MATRIX_LLM_BLEND_MODE=blend`): agree → trade at the mean confidence;
  only one side has a view → trade at 0.6× confidence; opposite views → hold.
  The alternative arms (`llm_overrides`, `rule_only`) exist for A/B.
- **Non-crypto markets zero out crypto-only signals** (funding, open interest)
  and renormalise the remaining weights.
- **Emission is capped by consumption** since 2026-09-13: a strategy may have at
  most `champion slots × 5` open unfilled predictions
  (`matrix_shared/backpressure.py`). Before that, `funding_reversion` alone
  produced ~9 000 expiring predictions a day against one slot.
- The agent additionally caps each market's universe at 60 symbols by realised
  edge, skips symbols it just decided HOLD on for 10 minutes, and skips symbols
  with no fresh price.

---
title: Strategies
updated: 2026-10-09
sources: [services/strategy/src/strategy/modules/, services/agent/src/agent/decide.py, "db: strategy_configs"]
status: current
---

Two producers of predictions: 12 deterministic modules (`strategy`) and one
LLM agent (`matrix_agent`). Both write into the same `predictions` table and
are measured identically.

## Claims
- **Crypto modules**: `funding_reversion`, `grid`, `dca`, `oi_delta`,
  `oi_breakout`, `momentum_xs`, `cash_and_carry`, `screener_follow`, plus
  `inverse_carry`, `xexch_funding_arb`, and the shadow-only
  `neg_funding_carry` (2faba91; the one candidate, [[signal-research-2026-10]]).
  **Status 2026-10-09** (`strategy_configs`): active crypto `dca`,
  `funding_reversion`, `inverse_carry`, `matrix_agent`, `oi_breakout`,
  `oi_delta`, `screener_follow`; shadow `inverse_carry`, `momentum_xs`,
  `neg_funding_carry`; retired `grid` (09-20), the `momentum_xs` champion
  (09-21), `cash_and_carry` and `xexch_funding_arb` (10-09). All five BIST
  configs and matrix_agent/us are `paused` ([[market-cadence-study]]). None
  of the active ones has a measured edge ([[strategy-scoreboard]]).
  **BIST/US modules**: `gap_fade`, `intraday_reversion`, `volume_breakout`,
  `news_event`.
- **Worst contributors, 30 d to 2026-09-19**: `funding_reversion`
  (1 007 trades, −80.65 USD, −21.3 bps), `grid` (899, −27.67, −11.9),
  `momentum_xs` (204, −15.98, −40.0), `oi_delta` (278, −15.82, −13.9). Trade
  count and loss track each other almost exactly, which is the signature of a
  cost-dominated book ([[pnl-reality]]).
- **A config is `active`, `shadow`, `paused` or `retired`.** `paused` (since
  2026-10-09) is skipped by both the agent and the strategy service, so it is
  reversible without losing the row. `active` is the champion;
  `shadow` is a challenger running in parallel on the shadow wallet; the
  efficacy job promotes or retires it ([[learning-loop]]). A strategy with no
  active config emits nothing.
- **The agent runs rule-only since 2026-10-09** (`MATRIX_LLM_BLEND_MODE=rule_only`,
  6a0884e; no LLM call is made), every 60 s (9091f38). The LLM added no
  measurable direction or selection skill over the rule lean in the same
  hours ([[llm-value-audit]]). `blend` (agree → mean confidence; one view →
  0.6×; opposite → hold) and `llm_overrides` remain for an A/B.
- **Non-crypto markets zero out crypto-only signals** (funding, open interest)
  and renormalise the remaining weights.
- **Emission is capped by consumption** since 2026-09-13: a strategy may have at
  most `champion slots × 5` open unfilled predictions
  (`matrix_shared/backpressure.py`). Before that, `funding_reversion` alone
  produced ~9 000 expiring predictions a day against one slot.
- The agent additionally caps each market's universe at 60 symbols by realised
  edge, skips symbols it just decided HOLD on for 10 minutes, and skips symbols
  with no fresh price.

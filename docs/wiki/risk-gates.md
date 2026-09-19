---
title: Risk gates
updated: 2026-09-19
sources: [packages/python-shared/src/matrix_shared/{live_gate,trading_safety,exchange_shadow}.py, docs/TRADING.md]
status: current
---

What stands between this code and real money. These are enforced in code, not
in documentation, and they are the one part of the system that is not
optimisation-eligible.

## Claims
- **One gate, one file.** `matrix_shared/live_gate.py` is the single decision
  point: posture, `LIVE_EXECUTION_ENABLED`, a valid certificate, circuit state,
  and capital caps. `execution/safety.py` and `exchange_shadow.py` delegate to
  it; there is no second path to an order.
- **Certificate** (`paper_trade_certificate`) requires, by default: 60
  observation days, 200 outcomes, win rate ≥ 0.40, total PnL > 0, drawdown
  ≤ 15 %, and — since 2026-09-13 — a **95 % CI lower bound on mean PnL per
  trade above zero**. A positive total built on a few lucky trades does not
  qualify. Certificates granted with loosened env thresholds are stamped
  `+relaxed` and are refused on mainnet. A version that later breaches the
  drawdown cap is auto-revoked on the next reflection pass.
- **Circuit breaker**: daily loss beyond the wallet's limit flattens open
  positions and blocks opening. In live mode it does not auto-reset; a human
  (or `make circuit-reset` / Telegram `/circuit_reset`) re-arms it.
- **Three human-only decisions**, by design and forever:
  `LIVE_EXECUTION_ENABLED`, `LIVE_CAPITAL_CAP_USD`, mainnet API keys.
- **Forbidden paths**: the dev_agent may edit anything except
  `matrix_shared/trading_safety.py`, `matrix_shared/exchange_shadow.py` and
  `execution/safety.py`. Touching them fails its task.
- **Reflection cannot touch risk caps**: `reflection/parsing.py` strips
  `max_position_pct`, `daily_loss_circuit_pct`, `max_concurrent_positions`,
  `live_capital_cap_usd` and `live_execution_enabled` from any model output,
  and the promoter scrubs them again on apply.

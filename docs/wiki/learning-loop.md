---
title: Learning loop
updated: 2026-09-20
sources: [services/reflection/, services/labs/, services/agent_lessons/, packages/python-shared/src/matrix_shared/{setup_memory,regime,stats,backpressure}.py]
status: current
---

How a measured outcome becomes a changed decision. Every arc here has a
statistical gate in front of it, because the failure mode of an automatic tuner
is optimising noise.

## Claims
- **Reflection (600 s)** builds a 24 h metrics window per active config and
  proposes at most one mutation. Gates, in order: the strategy must be
  *underperforming*; there must be no live challenger and no pending proposal
  for that version (otherwise the tick spends an LLM call on something that
  cannot be applied); deterministic strategies additionally need n ≥ 30 and a
  24 h cooldown between parameter tunes. The objective in the prompt is
  **realised total PnL after costs**, with win rate and score as diagnostics.
  The prompt also carries the exit-reason mix, which is the geometry signal
  ([[pnl-reality]]).
- **Labs** runs a genetic search over parameters and scores genomes on
  `mean − k·std/√n` so a lucky small sample cannot win. It auto-applies safe
  proposal types; since 2026-09-13 LLM-authored `threshold_change`/`weight_tune`
  proposals also auto-apply — but only as **challengers**, and only if every key
  already exists in the champion's params.
- **Champion/challenger** is the only way a parameter change reaches capital.
  The challenger runs on the shadow wallet under the champion's slots;
  `reflection.efficacy` compares them and either cuts over or retires. Repeated
  retirements for one strategy escalate into a `dev_tasks` row for the
  [[development]] to rewrite the signal logic.
- **Efficacy** also measures every applied proposal before/after with a z-test
  and auto-reverts negatives, marking the version `recently_reverted` so the
  tuner does not immediately re-propose it.
- **Lessons** (`agent_lessons`) distil outcomes into `avoid`/`prefer` patterns
  per symbol/side/regime, with a confidence gate (0.4), a TTL (14 d) and
  contradiction retirement. An `avoid` veto is bypassed ~25 % of the time on
  exploration trades so the lesson keeps being tested rather than becoming a
  self-fulfilling lock. Operator directives (`OPERATOR:` lessons, written from
  Telegram) are never bypassed.
- **Setup memory** answers "the last k times this symbol looked like this".
  `matrix_shared/setup_memory.py` projects each prediction's feature snapshot
  into a 14-dimensional normalised vector and does cosine k-NN over the same
  symbol/strategy/side, falling back to the whole asset class when the symbol's
  own history is thin. A `good` verdict adds 0.10 confidence, `bad` halves it;
  it never flips a side. No embedding provider is needed.
- **Regime** (`vol/trend/funding`, e.g. `low/flat/neutral`) tags every
  prediction and keys lessons. Reference symbols fall back to liquid
  constituents when the index proxy is not ingested.
- **Slot scorer** moves capital between strategies, but only on n ≥ 30 closed
  positions and using a **Wilson lower bound** win rate; the consecutive-loss
  auto-cut is the one change allowed on thinner evidence.
- **The loop closed end to end on evidence for the first time on 2026-09-20**:
  the alpha-decay study measured `momentum_xs` peaking at 90 minutes against
  its configured 60 (t=3.59) → a `param_tune` proposal was filed with that
  evidence in its `metrics_window` → the tied challenger occupying the slot was
  retired under the new indifference rule → labs applied the proposal as
  challenger v4 → efficacy now judges it on realised PnL. No step was taken by
  hand.
- **Honest limitation:** all of the above is machinery for exploiting an edge.
  It cannot manufacture one. As of 2026-09-20 exactly one strategy has a
  measured edge ([[edge-study]]), and the machinery is now pointed at it.

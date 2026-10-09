---
title: Learning loop
updated: 2026-10-09
sources: [services/reflection/, services/labs/, docs/wiki/learning-loop-statistics.md, services/agent_lessons/, packages/python-shared/src/matrix_shared/{setup_memory,regime,stats,backpressure}.py]
status: current
---

How a measured outcome becomes a changed decision. Every arc here has a
statistical gate in front of it, because the failure mode of an automatic tuner
is optimising noise.

## Claims
- **Reflection (600 s)** builds a 24 h metrics window per active config and
  proposes at most one mutation. Gates, in order: the strategy must be
  *underperforming* — since 2026-10-09 the one-sided 95 % upper bound of
  realised net USD per episode below zero, ε-probes excluded
  ([[learning-loop-statistics]]); there must be no live challenger and no pending proposal
  for that version (otherwise the tick spends an LLM call on something that
  cannot be applied); deterministic strategies additionally need n ≥ 30 and a
  24 h cooldown between parameter tunes. The objective in the prompt is
  **realised total PnL after costs**, with win rate and score as diagnostics.
  The prompt also carries the exit-reason mix, which is the geometry signal
  ([[pnl-reality]]).
- **Labs** runs a genetic search over parameters. Until 2026-10-09 it ranked
  and bred on `mean − k·std/√n` at n ≥ 5, which selected luck (a zero-edge
  simulation promoted ~18 genomes a month); it now ranks on the
  empirical-Bayes posterior of each genome's excess over the genomes trading
  in the same hour, breeds only at ≥ 20 episodes and promotes on
  pre-registered, day-clustered looks ([[learning-loop-statistics]]). It auto-applies safe
  proposal types; since 2026-09-13 LLM-authored `threshold_change`/`weight_tune`
  proposals also auto-apply — but only as **challengers**, and only if every key
  already exists in the champion's params.
- **Champion/challenger** is the only way a parameter change reaches capital.
  The challenger runs on the shadow wallet under the champion's slots;
  `reflection.efficacy` compares them and either cuts over or retires. Repeated
  retirements for one strategy escalate into a `dev_tasks` row for the
  [[development]] to rewrite the signal logic.
- **Efficacy** also measures every applied proposal before/after with a z-test
  (same per-episode sample as the mutation trigger; challenger cutover at
  z ≥ 1.645 since 2026-10-09) and auto-reverts negatives, marking the version `recently_reverted` so the
  tuner does not immediately re-propose it.
- **Lessons** (`agent_lessons`) distil outcomes into `avoid`/`prefer` patterns
  per symbol/side/regime, counted in episodes since 5f40b1d (a replay of all
  27 historical lessons scored 0.00–0.14 on episodes; **no lesson is active
  on 2026-10-09**), with a confidence gate (0.4), a TTL (14 d) and
  contradiction retirement. An `avoid` veto is bypassed ~25 % of the time on
  exploration trades so the lesson keeps being tested rather than becoming a
  self-fulfilling lock. (Measured 2026-10-09: the corridor has never fired;
  ε is now spent mainly in corridor cells — [[learning-loop-statistics]].) Operator directives (`OPERATOR:` lessons, written from
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
- **Slot scorer** moves capital between strategies, scoring each strategy's
  last 30 bets (episodes, not fills, since 6dcc8f1) with a **Wilson lower
  bound** win rate. It never promotes a strategy that has no active or shadow
  config or no signal in 24 h. The consecutive-loss auto-cut is the one change
  allowed on thinner evidence, and since 5e551a5 it can only cap slots
  (`min(old, 1)`), never grant one. A carry strategy's signals count as
  running (36b30cb).
- **The loop closed end to end for the first time on 2026-09-20**, mechanically:
  the alpha-decay study measured `momentum_xs` peaking at 90 minutes against
  its configured 60 (t=3.59) → a `param_tune` proposal was filed with that
  evidence in its `metrics_window` → the tied challenger occupying the slot was
  retired under the new indifference rule → labs applied the proposal as
  challenger v4 → efficacy judged it on realised PnL. No step was taken by
  hand. **The evidence it acted on is withdrawn** (2026-10-09, 5475933 /
  7fa47cd): the horizon profile counted re-emitted rows. The machinery worked,
  but it ran on a phantom edge.
- **Honest limitation:** all of the above is machinery for exploiting an edge.
  It cannot manufacture one. ~~As of 2026-09-20 exactly one strategy has a
  measured edge~~ (withdrawn 2026-10-09: momentum_xs was pseudo-replication).
  As of 2026-10-09 no strategy in the book has one ([[edge-study]]), and the
  only candidate is the shadow `neg_funding_carry`.

- **A measurement that is missing is not a measurement of zero.** The slot
  scorer reads `strategy_edge`; that cache lived only in the process, so for
  minutes after every reload it answered `None`, and `None` demoted rather than
  abstained. On 2026-09-20 a restart took `momentum_xs` from 7 slots to 3
  seconds after the same pass had promoted it on a `confirmed` +29.7 bps edge
  (that edge was later withdrawn as pseudo-replication; the persistence lesson
  stands).
  The cache now persists to the model volume with wall-clock stamps. The
  general rule, and it applies to every gate here: distinguish *unknown* from
  *measured and bad* before acting on it.

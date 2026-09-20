---
title: Quant methods — what is applied, what is next
updated: 2026-09-20
sources: ["Bailey & López de Prado, The Deflated Sharpe Ratio (SSRN 2460551)", "López de Prado, Advances in Financial Machine Learning (triple barrier, meta-labeling)", packages/python-shared/src/matrix_shared/{edge_study,barrier_study,barriers}.py]
status: current
---

Established methodology this system uses, why each one was adopted (always in
response to a measured failure, never as decoration), and what is queued.

## Applied

- **Controlled entry-timing test** — [[edge-study]]. A strategy is compared
  against *itself with random entry times*, same symbol, side, barriers and
  horizon. This is the only way to separate "the signal works" from "the market
  drifted". It is what showed `momentum_xs` +30.8 bps over random while its
  fills were −36 bps.
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
  round trip. Replay: `oi_breakout` −6.7 → +14.1 net bps, `momentum_xs`
  +15.7 → +26.9.
- **Multiple-testing correction** (Benjamini-Hochberg at FDR 5 %) — testing 13
  strategies at once means an uncorrected 5 % threshold yields roughly one
  false discovery per run, the mechanism behind backtests that never repeat
  (Bailey & López de Prado). Applied to every edge report: on 2026-09-20 only
  **2 of 13** survived, and one of those has n=33.
- **Signal freshness gate** — [[paper-engine]]. A bracket is only the trade the
  strategy proposed if it is opened promptly; fills 20–53 % into the horizon
  were the largest measured value destroyer.
- **Cost-aware gating everywhere** — an edge is only real if it exceeds the
  round trip (15 bps crypto). The EV floor, the slot gate and the barrier study
  all compare against that number rather than against zero.
- **Wilson lower bounds and CI-gated certificates** — [[learning-loop]],
  [[risk-gates]]: small-sample win rates are not point estimates.

## Queued, in order of expected value

1. **Meta-labeling** (López de Prado). Keep `momentum_xs` as the primary model
   deciding *side*, and train a secondary model to decide *whether to act* on
   each of its signals. The system already has the required labels (triple
   barrier outcomes) and features (`predictions.context.features`), and can
   only fill ~12 % of signals — so "which ones" is exactly the decision worth
   optimising. Published results move precision substantially; here it would
   replace the EV ranker, which measurement shows adds nothing.
2. **Purged, embargoed cross-validation** before any model is trusted:
   overlapping horizons make naive k-fold leak by construction.
3. **Volatility targeting for position size** — size ∝ 1/σ so risk per trade is
   constant across regimes, instead of the current confidence-scaled notional.
4. **Deflated Sharpe ratio** as the promotion bar for challengers, replacing
   raw PnL comparison.
5. **Execution realism**: measure fill slippage against the mark to validate
   the assumed 5.5 + 2 bps before any live capital.

## Open questions
- Does the meta-labeller beat "fill the freshest signal", the policy the
  freshness gate now implements by default?

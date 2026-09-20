---
title: Edge study — do the entries carry signal?
updated: 2026-09-20
sources: [packages/python-shared/src/matrix_shared/edge_study.py, "make edge-report DAYS=14 DRAWS=30", "db: predictions ⋈ paper_positions delay analysis 2026-09-20"]
status: current
---

A controlled experiment that answers the prior question behind
[[pnl-reality]]: before asking whether a strategy is *tuned* well, ask whether
its entries beat chance at all.

## Method
One simulator, two entry-time distributions. Every prediction is replayed on 1m
bars (`simulate_bracket`: take-profit wins ties, stop-loss, else exit at the
horizon close), then replayed again from K random entry times on the **same
symbol, side, take-profit, stop-loss and horizon**. Costs, exit rules, holding
time and symbol mix are identical by construction, so the only variable left is
*when* the position was opened. Arms are compared with a Welch t-test. Returns
are gross; the round-trip cost (15 bps for crypto) is printed alongside, since
an edge must clear it to be worth trading.

**Signals, not fills.** Only ~12 % of predictions ever become positions, so
judging a strategy by its fills discards seven eighths of its evidence — and a
strategy demoted to zero slots would never produce evidence again. Since
2026-09-20 every prediction is replayed, filled or not. This changed the
answers (below), and it is what makes free evaluation of a demoted strategy
possible.

## Findings — 14 days to 2026-09-20, 5 277 signals, both nulls

Each strategy is tested against two nulls: random entry time with the same
side, and random side at the same moment. A strategy that beats neither has no
measured reason to hold capital.

| strategy | n | vs random time | t | vs random side | t |
|---|---|---|---|---|---|
| **momentum_xs / crypto** | 799 | **+31.2** | **5.25** | **+33.0** | **5.55** |
| bist_news_event / bist | 33 | +21.1 | 5.39 | +10.6 | 4.04 |
| oi_breakout / crypto | 459 | +5.6 | 0.93 | **−14.9** | −2.46 |
| oi_delta / crypto | 723 | +4.2 | 1.11 | −5.2 | −1.38 |
| grid / crypto | 800 | +2.7 | 1.25 | +2.5 | 1.15 |
| funding_reversion / crypto | 799 | +1.2 | 0.49 | −2.1 | −0.84 |
| dca / crypto | 761 | −2.7 | −1.21 | **−5.8** | −2.56 |
| matrix_agent / us | 48 | −10.9 | −1.43 | −13.6 | −1.78 |

## Claims
- **`momentum_xs` has the strongest signal in the book** (+36.0 bps over random
  — and its *fills* were the worst thing in the book
  (−35.8 bps measured on 2026-09-19 against the same control). The signal was
  real; what reached capital was not.
- **The destroyer is fill latency, and it is measured.** Average fill happened
  this far into the prediction's own horizon (14 d to 2026-09-20):
  momentum_xs 53 %, dca 41 %, bist_gap_fade 40 %, grid 40 %, oi_breakout 39 %,
  oi_delta 32 %, matrix_agent 24 %, funding_reversion 22 %; worst cases 99–100 %.
  Unfilled predictions kept competing for slots until `close_by`, so a
  one-hour momentum call was routinely opened 32 minutes late. Fixed by the
  freshness gate in [[paper-engine]].
- **The earlier fills-only conclusion was an artefact of that bias.**
  `oi_delta` looked like the one edge (+19.4 bps on fills) because its fill
  rate was 42 % and its horizon short; on the full signal set it is +1.3 bps
  (t=0.34). Do not size on fill-sampled edge.
- **`oi_breakout` and `dca` pick direction worse than a coin flip**
  (−14.9 t=−2.46, −5.8 t=−2.56): inverted, not mistuned. `grid` and
  `funding_reversion` beat neither null and produce most of the volume. Volume without edge is
  the cost engine described in [[pnl-reality]].
- **Multiple testing is corrected, not hand-waved.** Since 2026-09-20 the
  report applies Benjamini-Hochberg at FDR 5 % across all 13 simultaneous
  comparisons: **2 of 13 survive** — `momentum_xs` (p<0.0001) and
  `bist_news_event`, the latter on n=33, which is too thin to allocate against.
  See [[methods]].
- **Remaining caveats.** Controls are drawn from the same period, so market
  drift appears in both arms. The simulator enters at a 1m bar close and does
  not model slippage beyond the flat cost assumption.

## How it is used
`make edge-report [DAYS=14] [STRATEGY=x] [DRAWS=20]` prints the table. The slot
pass consults the same study through `_entry_edge_verdict` (cached 6 h):
`pays` exempts a strategy from the realised-loss demotion, `harmful` pulls one
from the book even when its realised PnL looks survivable. See
[[learning-loop]].

## Open questions
- Does `momentum_xs`'s +36 bps survive now that fills are fresh? This is the
  first falsifiable profit hypothesis the system has had: same signal, same
  costs, only the latency removed.
- How much of the remaining gap is slippage the simulator does not model?
- Would the cost-engine strategies become positive at a much higher signal
  threshold (fewer, better trades), or is their signal empty at every threshold?

---
title: Research backlog
updated: 2026-09-20
sources: ["external literature sweep 2026-09-20", "make edge-report / barrier-report / meta-report"]
status: current
---

Methods not yet applied, ranked by expected value for *this* system's measured
state: one strategy with verified edge, a book otherwise dominated by costs.
Each entry carries the single decisive experiment, so nothing is adopted on
authority. Applied methods live in [[methods]].

## 1. ~~Post-only entry~~ — TESTED 2026-09-20, rejected for the strategy that matters
`make execution-report` replayed every signal resting a limit at the signal
bar's close, filling as maker when the market traded through it and crossing
otherwise. Fill rates were high (82–91 %) and most strategies gained +2…+16 bps.
**But `momentum_xs` — the one strategy with verified edge — lost 2.9 bps**
(t=−0.35): a momentum signal's limit only fills when price comes back to it,
i.e. exactly when the momentum has broken. Textbook adverse selection, and it
outweighs the 6.5 bps of fee saved. Crossing remains correct for it.
The gains elsewhere are irrelevant while those strategies hold no capital.
*Still open:* the **exit** leg. A take-profit is a resting limit order and is
being charged taker fees by our cost model; correcting that accounting would
change the EV floor for every strategy. Do not implement as a PnL improvement —
implement it only alongside actually placing limit take-profits.

## 2. Strategies that beat neither null — demoted, deliberately not deleted
Eleven of thirteen beat neither null and two pick direction worse than a coin
flip. All are at **zero slots**, so they hold no capital and cost nothing but
database rows. Formal retirement was considered and rejected: a retired config
stops emitting predictions, and predictions are the free evidence every study
runs on ([[edge-study]] evaluates signals, not fills). A strategy that cannot
trade but still produces signal can be re-evaluated for nothing and revived if
the evidence turns; a deleted one is gone. The one real cost is `matrix_agent`,
which spends LLM calls per signal — its universe cap and HOLD cooldown bound
that, and it is worth re-examining if the rate budget ever binds.

## 3. ~~Horizon profiling~~ — MEASURED 2026-09-20, one change proposed
`make horizon-report` measures each signal's **excess** return over a random
entry in the same symbol at h = 1…120 min (subtracting per-symbol drift, or a
rising tape reads as slow alpha), net of cost, with a t per horizon.
Only one result clears its own noise: `momentum_xs` peaks at **90 minutes,
+37.0 bps, t=3.59**, against −26.8 bps at its configured 60. Filed as a
`param_tune` proposal (horizon_s 3600 → 5400); it waits because momentum_xs
already has a challenger and the system allows one at a time.
Everything else has t < 2 — long horizons carry enormous variance and always
win on the point estimate alone, which is why the report prints `act` rather
than just the argmax. Note the multiplicity: 12 horizons × 7 strategies is 84
comparisons, so treat any t near 2 as noise.

## 4. ~~Per-symbol cost model~~ — DONE 2026-09-20
`matrix_shared/symbol_costs.py` measures the median top-of-book spread per
symbol from our own snapshots and charges half of it per side, refreshed each
reflection tick, cached on the shared volume, falling back to the flat
allowance for anything unmeasured. Live result: BTCUSDT 12.0 bps round trip,
ADAUSDT 15.5, BRUSDT 16.9. *Still open:* impact beyond the touch (our clips are
small, so the half-spread is the dominant term) and validation against real
fills, which needs live execution.

## 5. ~~Quarter-Kelly sizing on a shrunk edge~~ — SHIPPED 2026-09-20
`allocation.kelly_fraction_of_equity` sizes any strategy the controlled study
calls `pays`, on the **95% lower bound** of its measured edge net of the
measured round trip ([[methods]]), divided by the book's real concurrency,
quartered, then capped. Kelly is ~11x more sensitive to errors in means than
variances, so the lower bound rather than the point estimate is the whole
defence. The wallet's `max_position_pct` remains the ceiling in every branch,
and a zero fraction falls back to the old sizing instead of suppressing a trade
the slot gate allowed.

Live on the first day: `momentum_xs` measures +33.2 bps at t=5.56 with a
per-trade sd of 164.8 bps over 798 signals. Quarter-Kelly on eight concurrent
positions asks for ~6.7% of equity; the 5% hard cap and then the 2% wallet gate
both bind, so it sizes at the gate — roughly 2.3x its previous effective size.
Two things follow: the *policy*, not the arithmetic, is setting this size, and
the block-bootstrap comparison across f is still the open question, now
answerable against realised trades rather than in the abstract.

*Still open:* the f ∈ {0.1, 0.25, 0.5, 1.0} bootstrap, and the correlation
correction — eight crypto perps are nowhere near eight independent bets, which
is the assumption the divisor makes.

## 6. Queue- and imbalance-conditioned placement
Condition passive placement and repricing on book imbalance. Front-of-queue 1 s
markout −0.06 bp vs −1.16 bp at the back on Binance BTC perp. Prerequisite:
L2 snapshots at ≤ 1 s — **blocked until capture is upgraded**. Only worth doing
after #1 works.

## 7. Deflated Sharpe + BHY as the promotion bar
Our 13 tests share overlapping data, so Benjamini-Hochberg's independence
assumption is violated; BHY is ~3.2× stricter at M=13 and `momentum_xs` clears
it anyway. *Action:* pre-register the trade count at which a halved
(winner's-curse) edge would still be significant — about 900 trades — and kill
the hypothesis if unreached.

## Judged dead ends for this system
VPIN (mechanically a function of trading intensity); liquidation-cascade
prediction (no ex-ante signature survives testing); open-interest and funding
as sub-hour directional signals (funding is an 8 h average by construction —
carry is a multi-day book); cross-exchange lead-lag (measured at 0.055 s, a
colocation business, unreachable from a 15 s loop); HMM regime gating (n=799
cannot fund it); CPCV/PBO (they discipline hyperparameter searches — our
permutation test is stronger evidence for a single pre-specified hypothesis;
revisit when the lab grid-searches genomes); intraday volatility targeting as a
return enhancer (treat it as drawdown control only). Order-flow imbalance is
real but is a maker-quoting feature: published crypto gross edges cluster at
0.4–3 bps against a ~10 bps floor.

## Open questions
- Does #1 survive adverse selection on our actual symbol mix, most of which is
  altcoin perps rather than BTC/ETH?

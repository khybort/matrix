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

## 1. Post-only entry with a timed taker fallback — highest certainty
Rest passive at touch for 10–60 s, cross only if unfilled. Maker/maker round
trips run ~3–4 bps against ~9–11 bps taker/taker, so this recovers **6–7 bps of
round trip — about a fifth of the entire measured +31 bps edge** without
finding any new signal. Affordable precisely because horizons are 5–60 minutes.
*Decisive test:* replay the 799 `momentum_xs` signals with a post-only-then-cross
policy, counting non-fills as zero. **Pass: net edge ≥ +10 bps and fill rate
≥ 60 %.** Watch for adverse selection — passive fills arrive when the book
turns against you; crypto evidence puts that at 0.5–1.2 bp against 3.5 bp/side
saved.

## 2. Delete the strategies that beat neither null
Eleven of thirteen beat neither the random-time nor the random-side null
([[edge-study]]), and two are directionally *worse* than a coin flip. They each
pay 15 bps to trade noise. *Decisive test:* recompute 30-day book PnL with
survivors only — arithmetic, not a hypothesis. The slot gate already demotes
them; formal retirement of the configs is the remaining step.

## 3. Horizon profiling (alpha decay)
Measure E[return | signal] at h = 1…120 min net of cost and set the horizon to
the argmax instead of a fixed value. **57 % of exits are time exits at roughly
minus cost** — the loudest unexplained diagnostic left. *Decisive test:*
split-half — fit the decay profile on one half, apply the argmax horizon to the
other. **Pass: ≥ +8 bps/trade and time-exit share < 40 %.**

## 4. Per-symbol cost model replacing the flat 15 bps
The flat assumption is simultaneously too harsh for BTC/ETH (~10–11 bps all-in
at small clip) and far too generous for altcoin perps (15–40 bps), which
corrupts the EV floor in both directions. *Decisive test:* measure realised
round-trip cost per symbol from fills and book depth; re-apply the EV floor.
**Pass: a symbol whitelist where measured edge > measured cost, tracking error
< 3 bps.**

## 5. Quarter-Kelly sizing on a shrunk edge
f = 0.25 × Kelly on an empirical-Bayes-shrunk edge, scaled inversely to
realised volatility, hard-capped. Kelly is ~11× more sensitive to errors in
means than variances, and fractional Kelly is equivalent to full Kelly on a
shrunk mean. *Decisive test:* block-bootstrap realised trade returns at
f ∈ {0.1, 0.25, 0.5, 1.0}. **Pass: higher median terminal log-wealth with the
95th-percentile drawdown inside the existing cap.** Sizing creates no edge; it
only stops one being destroyed.

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

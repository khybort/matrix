---
title: Edge study — do the entries carry signal?
updated: 2026-09-19
sources: [packages/python-shared/src/matrix_shared/edge_study.py, "make edge-report DAYS=14 DRAWS=60"]
status: current
---

A controlled experiment that answers the prior question behind
[[pnl-reality]]: before asking whether a strategy is *tuned* well, ask whether
its entries beat chance at all.

## Method
One simulator, two entry-time distributions. Every closed trade is replayed on
1m bars (`simulate_bracket`: take-profit wins ties, stop-loss, else exit at the
horizon close), then replayed again from K random entry times on the **same
symbol, side, take-profit, stop-loss and horizon**. Costs, exit rules, holding
time and symbol mix are identical by construction, so the only variable left is
*when* the position was opened. Arms are compared with a Welch t-test. Returns
are gross; the round-trip cost (15 bps for crypto) is printed alongside, since
an edge must clear it to be worth trading.

## Findings — 14 days to 2026-09-19, 2 839 trades, 60 control draws

| strategy | n | gross bps | control bps | edge | t |
|---|---|---|---|---|---|
| **oi_delta / crypto** | 274 | +19.7 | +1.6 | **+19.4** | **2.90** |
| matrix_agent / crypto | 168 | +7.9 | +2.0 | +6.0 | 0.80 |
| grid / crypto | 899 | −0.1 | +0.8 | −0.9 | −0.42 |
| funding_reversion / crypto | 1 007 | +1.2 | +4.8 | −3.6 | −1.40 |
| dca / crypto | 98 | −14.4 | −2.4 | **−12.0** | −2.21 |
| momentum_xs / crypto | 204 | −23.4 | +12.4 | **−35.8** | −2.78 |
| matrix_agent / us | 23 | −27.9 | +2.6 | −30.5 | −3.46 |

## Claims
- **`oi_delta` is the only strategy whose entries beat random by more than they
  cost** (+19.4 bps against a 15 bps round trip, t=2.90). Its realised PnL was
  negative anyway — the loss came from cost and sizing, not from the signal.
  This is the one thread worth pulling.
- **`momentum_xs` and `dca` are significantly *worse* than random.** They are
  anti-timed, not mistimed; no threshold or sizing change repairs a negative
  edge, so they belong out of the book.
- **`funding_reversion` and `grid` make 67 % of all trades with gross ≈ 0.**
  They are pure cost engines: every trade pays ~15 bps for a coin flip. This is
  where most of the book's bleed comes from, and it is not a bug to fix — it is
  an absence of signal.
- **Caveats, stated so nobody over-reads the table.** 13 comparisons were made,
  so at α=0.05 roughly one false positive is expected; a Bonferroni-strict
  threshold would be |t| ≈ 2.9, which `oi_delta` just reaches and the negatives
  do not. The treatment arm enters at a 1m bar close while the live engine
  entered at tick price. Controls are drawn from the same period, so a strong
  market drift shows up in both arms (it is why `momentum_xs`'s control is
  +12.4). Confirm `oi_delta` on fresh data before sizing up.

## How it is used
`make edge-report [DAYS=14] [STRATEGY=x] [DRAWS=20]` prints the table. The slot
pass consults the same study through `_entry_edge_verdict` (cached 6 h):
`pays` exempts a strategy from the realised-loss demotion, `harmful` pulls one
from the book even when its realised PnL looks survivable. See
[[learning-loop]].

## Open questions
- Does `oi_delta`'s edge survive out of sample, and is it big enough after
  slippage on real fills?
- Would the cost-engine strategies become positive at a much higher signal
  threshold (fewer, better trades), or is their signal empty at every threshold?

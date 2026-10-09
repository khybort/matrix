---
title: Maker execution — does resting the order turn any signal net positive?
updated: 2026-10-09
sources: ["tick replay 2026-10-09: Bybit public linear-perp trades (public.bybit.com/trading, 424 symbol-days, RPI prints excluded) x crypto non-shadow predictions 2026-09-12..09-30 07:00 UTC, one per episode; scripts + PREREG.txt kept in the session scratchpad, method below", "Bybit fee schedule VIP0 linear perps (maker 0.020 %, taker 0.055 %), checked 2026-10-09", "db: market_orderbook_snapshots L1 sizes 2026-10-09 12:00-13:30 UTC", "packages/python-shared/src/matrix_shared/execution_study.py"]
status: current
---

Crypto costs ~12–15 bps a round trip, and several strategies are positive
*gross* ([[strategy-scoreboard]], [[llm-value-audit]]). If entries and exits
could be made as resting (post-only) orders, would costs fall far enough to
make one of them pay? **No (2026-10-09).** Maker execution is a real, robust
saving of about 5–6 bps per episode, but no strategy is close enough to
breakeven for that to flip it, and nothing was built into the paper engine.

## Method (pre-registered before any return was computed)
- **Signals**: every crypto non-shadow prediction 2026-09-12 → 2026-09-30
  07:00 UTC, collapsed with `one_per_episode`. Later signals are excluded
  because VM egress died 09-30 07:27 and the strategies were deciding on stale
  inputs ([[incidents]]). 9 778 episodes; 9 526 scored, 252 unscorable (no
  print within 120 s, or TONUSDT, which has been delisted and has no archive).
- **Ticks**: our own `market_trades` only goes back to 2026-10-09 12:01
  (outage plus 7-day retention), so fills are replayed on **Bybit's public
  daily trade archive**, which holds every print with its aggressor side. RPI
  prints are dropped: retail-only liquidity cannot fill our order.
- **Quotes at time t**: the last sell-aggressor print is the bid and the last
  buy-aggressor print is the ask (both ≤ 120 s old; a crossed pair is fixed
  with one tick). The order is placed 4 s after `generated_at` (measured paper
  latency). The horizon is anchored at `generated_at + horizon`, as in the
  paper engine.
- **Fees**: Bybit VIP0 linear perps, taker 5.5 bps and **maker 2.0 bps, no
  rebate**. The spread is paid explicitly through the bid and ask, not as a
  flat slippage allowance.
- **Baseline (taker)**: cross at the touch. Take-profit at its level, stop-loss
  at the triggering print, horizon exit at the touch.
- **Resting-order fill rule**, always decided by *later* prints, never by the
  quote at placement:
  - `join` rests at the bid (long) or ask (short). It fills on a print
    strictly through the price, or once opposite-aggressor volume at the price
    exceeds that symbol's median L1 size (measured from our book snapshots
    2026-10-09) as a queue proxy.
  - `joinT` fills on a trade-through only.
  - `inside` rests one tick inside when the spread is ≥ 2 ticks. That puts the
    order alone at the front of the queue, so the first opposite-aggressor
    print at its price fills it.
- **Variants**: entry {join, joinT, inside} × wait T {5, 15, 60, 300 s} ×
  unfilled {`chase` = cross at T, `skip` = no trade, scores 0} × exit
  {taker, maker}. A maker exit is a resting take-profit (needs a print through
  it, or queue volume), a horizon exit rested at our own touch for 30 s and
  then crossed, and a taker stop. That is 48 maker variants plus 2 baselines,
  over 9 arms: **450 cells tested**.
- **Split by time**: train before 2026-09-21, holdout from then on. Per arm,
  the variant with the best train net was frozen and evaluated once on
  holdout. Pass: holdout net > 0 with **day-clustered** t ≥ 2.

## Result — the pre-registered test
| arm | train-best variant | train n | fill | train net (t) | taker train | holdout n (days) | fill | holdout net (t) | taker holdout |
|---|---|---|---|---|---|---|---|---|---|
| funding_reversion | join 5 s, maker exit, skip | 1179 | 0.51 | −4.9 (−4.4) | −17.4 | 78 (10) | 0.55 | +37.7 (1.73) | +27.6 |
| agent_llm | joinT 5 s, maker exit, skip | 100 | 0.35 | +6.6 (0.63) | −5.5 | 173 (8) | 0.47 | +5.6 (0.81) | +2.2 |
| agent_rule | joinT 5 s, maker exit, skip | 414 | 0.48 | −1.2 (−0.34) | −17.7 | 2 (2) | — | — | — |
| agent_explore | joinT 5 s, maker exit, skip | 1302 | 0.24 | −1.5 (−2.2) | −12.4 | 285 (10) | 0.41 | −2.9 (−0.67) | −8.4 |
| dca | join 5 s, maker exit, skip | 808 | 0.31 | −4.4 (−4.2) | −21.0 | 669 (10) | 0.54 | −4.5 (−1.87) | −11.0 |
| grid | joinT 5 s, maker exit, skip | 3465 | 0.31 | −3.3 (−4.3) | −16.3 | 0 | — | — | — |
| momentum_xs | join 15 s, maker exit, skip | 288 | 0.61 | −18.3 (−3.6) | −30.7 | 148 (1) | 0.66 | −12.8 (—) | −24.2 |
| oi_breakout | inside 5 s, maker exit, skip | 108 | 0.60 | −1.2 (−0.1) | −15.5 | 96 (9) | 0.77 | −32.6 (−2.0) | −44.0 |
| oi_delta | inside 5 s, maker exit, skip | 168 | 0.62 | −11.7 (−4.3) | −27.1 | 243 (10) | 0.77 | −12.7 (−1.2) | −25.0 |

Net in bps per **episode** (a skipped episode scores 0). Agent arms follow
[[llm-value-audit]]: `agent_llm` = method `llm` or `llm+rule`.

**Nothing passes.** The best holdout is funding_reversion at +37.7, t=1.73, on
78 episodes. That is a holdout in which its *taker* baseline was already +27.6;
under the same policy its train net was −4.9 (t=−4.4) on 1 179 episodes. The
post-hoc best holdout cell, funding_reversion `join 15 s skip` at +39.2
(t=2.2), was neither train-selected (train −6.9, t=−8.7) nor valid after 450
cells. agent_llm is the only arm positive in both halves, at +5…+8 bps with
t ≤ 1.34 over the full 13 days.

Every train winner is a `skip` variant with a 5 s wait. That is not maker
value: on a signal that loses money, filling a third of the time loses a
third as much. Read the skip numbers as "trade less", not "trade better".

## What maker execution is worth — paired on the same episodes
Each cell is maker minus taker on identical episodes, in bps with the
day-clustered t in brackets. Full window, n = 9 526.

| arm | maker exit only | join 5 s, chase | join 60 s, chase | inside 15 s, chase | join 60 s, on filled only |
|---|---|---|---|---|---|
| all arms | **+1.7 (16.4)** | **+3.6 (16.3)** | **+4.7 (10.4)** | **+4.6 (19.0)** | **+10.6 (32.1)** |
| funding_reversion | +1.6 (16.1) | +4.3 (20.3) | +5.7 (7.6) | +5.9 (7.5) | +12.5 (12.7) |
| grid | +2.0 (9.0) | +3.7 (12.2) | +4.1 (16.1) | +4.2 (9.9) | +10.5 (24.5) |
| dca | +1.2 (12.1) | +2.9 (4.9) | +4.7 (5.4) | +4.1 (4.6) | +9.9 (13.8) |
| agent_llm | +1.3 (2.3) | +2.5 (3.8) | +3.1 (3.3) | +3.3 (3.5) | +6.9 (8.2) |
| oi_delta | +1.4 (2.3) | +5.4 (1.8) | +9.5 (4.2) | +6.4 (4.1) | +13.5 (5.6) |
| momentum_xs | +1.8 (3.8) | +5.2 (5.0) | +7.5 (4.4) | +5.3 (2.9) | +11.0 (6.3) |

A filled maker entry saves ~10.6 bps against crossing on the same signal
(3.5 bps of fee plus the spread). Adverse selection and the chase on unfilled
orders give back about half of that. The net, **+4…+6 bps per episode**, is
robust in every arm. But most arms sit 12–30 bps below zero after taker cost,
so the saving moves none of them across. The exception is agent_llm, which is
at −0.6 bps at taker cost; its maker variants reach only +5…+8 bps, with
t ≤ 1.34.

## Fill rate, adverse selection, missed trades (join, maker exit, full window)
| arm | spread (median) | fill 5 / 15 / 60 / 300 s | AS 5 s | AS 300 s | missed, 5 s | missed, 300 s |
|---|---|---|---|---|---|---|
| funding_reversion | 6.4 | 0.5 / 0.7 / 0.8 / 0.9 | +31 | −72 | −10.9 | +33.5 |
| agent_llm | 2.0 | 0.4 / 0.6 / 0.8 / 0.9 | +17 | −18 | −6.3 | +11.5 |
| dca | 2.9 | 0.4 / 0.6 / 0.8 / 0.9 | +5 | −71 | −13.1 | +30.1 |
| grid | 3.1 | 0.3 / 0.5 / 0.7 / 0.9 | −11 | −42 | −13.8 | +17.0 |
| momentum_xs | 3.7 | 0.5 / 0.6 / 0.8 / 0.9 | −43 | −154 | −15.9 | +50.5 |
| oi_delta | 4.8 | 0.7 / 0.8 / 0.9 / 1.0 | −49 | −139 | −15.3 | +52.0 |
| oi_breakout | 5.2 | 0.6 / 0.8 / 0.9 / 1.0 | −51 | −122 | −21.9 | +72.5 |

- **AS** is the signed forward return (mid at placement → mid at the horizon)
  of filled episodes minus unfilled ones, in bps. Negative means the fills
  are the losers.
- **missed** is the taker net the unfilled episodes would have earned: the
  edge given up by skipping them.

At a 300 s wait the selection is severe in every arm. The orders that fill
are the ones the market came back to (−42 to −154 bps of forward return
against the unfilled), and the orders that never fill are the winners
(+11 to +73 bps). This is the momentum result of 2026-09-20
([[research-backlog]] #1) generalised to the whole book. At 5 s the
selection is mild or even favourable for funding_reversion, agent_llm and
dca, which is why short waits win.

`join` and `joinT` differ by < 1 bp everywhere. Fills are driven by
trade-throughs, so the L1 queue proxy hardly matters at these sizes.

## Claims
- **Maker execution does not make any strategy pay** (2026-10-09, 450 cells,
  train/holdout by time, day-clustered). Do not build it into the paper engine
  for PnL. Nothing was implemented.
- **It is a real cost lever once a signal exists**: rest at the touch for
  5–15 s, then cross. That is worth +4…+6 bps per episode (t ≥ 10 pooled), and
  maker take-profit exits add +1.7. A strategy needs to be within about 5 bps
  of breakeven at taker cost for this to matter. On this evidence none is.
- Do not wait long. Fill rates rise to ~90 % at 300 s, but the fills become
  the losers and the misses the winners.
- `make execution-report` (bar-based) is optimistic: touch-fills at the last
  price, entry at the fill bar's close, and a maker fee of 1 bp. The fee was
  corrected to 2 bps on 2026-10-09. Use this page's numbers.

## Open questions
- Revisit when a strategy measures gross within ~5 bps of its taker round
  trip on clean episodes. The decisive test is this replay, re-run on that
  strategy's signals with the then-current archive.
- The queue proxy uses today's L1 sizes for September. Real depth may differ;
  it barely binds here.

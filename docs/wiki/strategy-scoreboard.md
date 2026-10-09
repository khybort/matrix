---
title: Strategy scoreboard — what earns, measured on clean signals
updated: 2026-10-09
sources: ["make edge-report DAYS=14 (5475933)", "gap-guarded signal replay 2026-10-09 (matrix_shared.edge_study.simulate_bracket, one signal per episode, entry bar <= 3 min old, no gap inside the horizon)", "db: paper_positions, predictions, strategy_slot_configs, market_ticker_snapshots 2026-10-09"]
status: current
---

Every emitting strategy, judged on its **signals** rather than its fills (see
[[edge-study]]), after three corrections that each changed the answer.

## Method
- One signal per **episode**: a re-emission of the same (strategy, market,
  symbol, side) inside an earlier signal's horizon is the same bet, not new
  evidence. Re-emission rates over 30 days: funding_reversion 91 %,
  momentum_xs 83 %, oi_breakout 76 %, oi_delta 75 %, BIST breakout/reversion
  45–66 %; dca, grid and matrix_agent ~0 %.
- Entry at the last **closed** bar, and that bar must be at most 3 minutes old,
  with no gap inside the horizon window (wall-clock span <= 1.5x horizon + 5 min).
- Net = simulated bracket return minus the measured round trip (crypto
  12–15 bps, BIST 40 bps).

## Scoreboard — 14 days to 2026-10-09
| strategy | mkt | n | gross | net | t(net) | vs random time | t | vs random side | t |
|---|---|---|---|---|---|---|---|---|---|
| funding_reversion | crypto | 32 | +20.4 | +6.1 | 0.13 | +45.7 | 0.97 | +19.5 | 0.40 |
| dca | crypto | 351 | +4.0 | −8.9 | −2.42 | +4.1 | 1.07 | +4.8 | 1.53 |
| matrix_agent | crypto | 216 | +3.3 | −9.2 | −1.23 | +4.5 | 0.58 | +3.4 | 0.47 |
| matrix_agent | us | 109 | −5.4 | −13.4 | −4.08 | −6.2 | −1.82 | −4.3 | −1.26 |
| bist_gap_fade | bist | 50 | +9.3 | −30.7 | −2.82 | +15.0 | 1.33 | +7.7 | 0.72 |
| oi_delta | crypto | 133 | −22.6 | −36.0 | −2.34 | −24.2 | −1.56 | −22.6 | −1.48 |
| oi_breakout | crypto | 58 | −31.3 | −44.7 | −1.52 | −30.2 | −0.97 | −33.0 | −1.14 |
| bist_intraday_reversion | bist | 16 | −17.9 | −57.9 | −1.25 | +12.1 | 0.27 | −18.5 | −0.41 |
| matrix_agent | bist | 43 | −19.1 | −59.1 | −3.22 | −12.0 | −0.69 | −20.1 | −1.08 |
| bist_volume_breakout | bist | 120 | −21.9 | −61.9 | −8.26 | −17.6 | −2.33 | −23.2 | −2.94 |

30 days (more power, includes retired momentum_xs and grid): best is oi_delta
at +4.6 gross / −9.2 net (vs random time +3.3, t=0.43, n=389); grid −21.6 net
on n=3 425 (t=−23); momentum_xs −25.7 net on its 355 real episodes.

Carry family (settlement-accounted replay, 30 days, one per episode):
inverse_carry **+14.9 bps net, t=0.95, n=25**; xexch_funding_arb −25.6
(t=−3.56, n=40); cash_and_carry −31.1 (t=−6.26, n=95). See [[paper-engine]]
for why the books showed 0 % wins.

## Claims
- **No strategy in the book has a measured edge after cost** (2026-10-09).
  The one candidate found outside the book is `neg_funding_carry` (shadow,
  2026-10-09, [[signal-research-2026-10]]): historical edge, zero paper
  episodes so far.
  The best net figure is funding_reversion at +6 bps on 32 episodes, t=0.13 —
  indistinguishable from zero. The EV floor that skips every candidate is
  right; loosening it would buy the costs above.
- **The engine's two "winners" are a look-ahead artefact.** `make edge-report`
  (even after 5475933) shows matrix_agent/crypto **confirmed** at +44.8 bps
  (t=5.55, n=437) and bist_volume_breakout +116 bps (t=10.2). The edge study
  takes the last bar at or before `generated_at` without checking its age:
  when the series has a gap (outage, a symbol not aggregated for days) the
  "entry" is hours or days before the signal and the horizon runs across the
  gap into prices the strategy had already seen. bist_volume_breakout's 244
  such signals return +300 bps each — every one a take-profit — while its 281
  fresh-entry signals return −14 bps; matrix_agent/crypto's 211 stale ones
  +49…+98 against +6.6 fresh. A `confirmed` status flows into Kelly sizing and
  a full slot share ([[learning-loop]]), so this must be fixed in the study
  (entry-bar age and gap guard) before its next cache refresh.
- **oi_delta does not deserve capital.** Its realised paper +33 bps (n=56)
  came from which signals were filled: its filled signals replay at +29 bps
  gross (t=2.44, n=95) and its unfilled ones at −3.3, and on the full signal
  set it is +3.3 vs random (t=0.43, 30 d) and −24 (14 d). The promotion bar is
  not what blocks it; its own signal is. Realised on those same filled trades
  was −4.8 bps, so ~20 bps of the replay's gross never reached the wallet.
- **Re-emission was still live** in the strategy service on 2026-10-09; it is
  now dropped at persistence (`strategy.persist.drop_reemissions`), so the
  prediction table counts bets, not ticks.

## Open questions
- Is there edge in a *subset* of these signals that a different filter would
  find? The EV ranker is not it ([[open-questions]]: Spearman −0.06).
- ~~inverse_carry with honest accounting: does +15 bps net survive spot-borrow
  cost?~~ Answered 2026-10-09 in [[signal-research-2026-10]]: on a year of
  Bybit settlements, entering *after* a settlement ≤ −0.08 % on coins with a
  published borrow rate nets +104 bps/episode in train (t=20, n=3 464) and
  +135 in the holdout (t=10.2, n=1 105) after 30 bps fees and today's borrow
  (+65, t=4.9 at 3x borrow). inverse_carry itself mostly fired on coins with
  no Bybit spot margin. New shadow strategy `neg_funding_carry` carries the
  surviving rule; it has no paper evidence yet and nothing is promoted.

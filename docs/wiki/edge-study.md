---
title: Edge study — do the entries carry signal?
updated: 2026-10-09
sources: [packages/python-shared/src/matrix_shared/edge_study.py, "make edge-report DAYS=14 DRAWS=30", "entry-rule before/after, read-only, 30 d to 2026-10-09 16:46 UTC, episodes, 20 draws, seed 7 (lower-level functions; registry untouched)", "carry evidence read-only 2026-10-09 (carry_edge_rows over 90 d of paper positions, in-memory registry)", "edge study read-only rerun 2026-10-09 12:32 UTC, 30 d, per strategy, registry writes disabled (before = rows + no gap guard, after = episodes + gap guard)", "db: predictions ⋈ paper_positions delay analysis 2026-09-20", "db: momentum_xs signal→wallet gap decomposition 2026-10-09 (155 closed positions of 2026-09-21 ⋈ predictions ⋈ 1m bars)"]
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

**One bet, one sample (since 2026-10-09).** Signals are collapsed into
episodes before anything is simulated (`one_per_episode`): a signal is kept
only when no earlier kept signal of the same strategy, market, symbol and side
is still inside its horizon. A strategy that re-emits the same call every tick
is one bet the wallet can hold once, not hundreds of samples. The report keeps
the raw count as `n_raw` beside the episode count `n`.

**Entry rule: no part of the scored path precedes the signal (since
2026-10-09, second fix).** Bar `ts` is the bar's start. Every arm — treatment,
random-time control, random-side control — enters at the **open of the first
1m bar starting at or after `generated_at` + 4 s** (`ENTRY_LATENCY`, the
paper engine's measured fill latency; `MATRIX_EDGE_ENTRY_LATENCY_S`) and is
scored from that bar on (`simulate_bracket`, `entry_index`). A signal whose
next bar starts more than `MAX_ENTRY_AGE` (3 min) later sits before a hole and
is unscorable. The barrier, horizon, execution and meta-label studies use the
same rule (`entry_price`; vol for the barrier study from bars closed before
the entry bar; a post-only limit rests at the entry bar's open and can only
fill from the next bar).

History: the first rule entered at the close of the bar in force (a price the
strategy could not have seen). Its replacement entered at the close of the
last bar closed *before* the signal and scored from the bar in force — up to
60 s of pre-signal path inside the scored window. See the correction below.

## Correction — pre-signal path in the entry bar (2026-10-09)

The correctness review found that the second rule credited the minute in which
a signal fired to the strategy: for a momentum or breakout signal, the very
move that triggered it. The bias is not one-signed — a mean-reversion entry
fires *on* an adverse move, so it was charged that move. Measured on the same
30 days, same episodes and same random draws (seed 7, 20 draws), old rule vs
new, gross bps; `vs time` / `vs side` = lead over the null (t):

| strategy / market | n old/new | gross old → new | Δ | vs time old → new | vs side old → new |
|---|---|---|---|---|---|
| grid / crypto | 1460/1465 | −7.5 → **+3.0** | **+10.6** | −7.6 (−4.97) → +2.9 (+1.93) | −7.8 (−5.10) → +3.1 (+2.02) |
| funding_reversion / crypto | 1220/1224 | +4.2 → +3.5 | −0.7 | +2.8 (+1.04) → +1.7 (+0.66) | −1.0 (−0.36) → −0.6 (−0.24) |
| dca / crypto | 1066/1078 | +0.2 → +1.1 | +0.9 | +0.5 (+0.26) → +1.0 (+0.51) | −0.9 (−0.45) → −0.5 (−0.23) |
| matrix_agent / crypto | 659/659 | +5.0 → +4.9 | −0.1 | +3.4 (+0.79) → +2.8 (+0.66) | +2.0 (+0.47) → +4.2 (+0.97) |
| oi_delta / crypto | 384/398 | +5.0 → **−6.9** | **−11.9** | +4.3 (+0.55) → −6.2 (−0.85) | +1.9 (+0.24) → −10.8 (−1.48) |
| momentum_xs / crypto | 277/287 | −18.4 → **−27.6** | **−9.3** | −17.4 (−1.54) → −32.1 (−2.92) | −24.8 (−2.19) → −28.8 (−2.62) |
| bist_volume_breakout / bist | 202/218 | −13.2 → −10.0 | +3.2 | −10.0 (−1.68) → −7.8 (−1.29) | −15.4 (−2.56) → −9.9 (−1.61) |
| oi_breakout / crypto | 188/195 | −7.3 → −6.5 | +0.8 | −9.3 (−0.67) → −9.5 (−0.72) | −15.7 (−1.13) → −6.7 (−0.50) |
| bist_gap_fade / bist | 109/119 | +15.2 → +7.0 | −8.3 | +15.0 (+1.59) → +8.2 (+0.91) | +17.6 (+1.89) → +7.9 (+0.89) |
| matrix_agent / us | 86/85 | −1.5 → −4.9 | −3.4 | −1.3 (−0.32) → −4.4 (−1.03) | +0.6 (+0.15) → −3.5 (−0.84) |
| bist_intraday_reversion / bist | 56/57 | +25.0 → +18.1 | −6.9 | +33.3 (+1.74) → +21.3 (+1.17) | +34.4 (+1.83) → +21.1 (+1.18) |
| bist_news_event / bist | 11/16 | +2.9 → +2.8 | −0.0 | +20.9 (+1.86) → +2.8 (+0.33) | +9.1 (+2.49) → +6.1 (+1.90) |

(`matrix_agent / crypto` is 659, not the 1 054 in the table below, because
exploration probes left the study in ad03b11.)

Reading:
- The breakout/momentum family was **flattered** by the pre-signal minute:
  oi_delta −11.9, momentum_xs −9.3, bist_gap_fade −8.3, bist_intraday_reversion
  −6.9 bps. momentum_xs is now significantly *worse* than random entry
  (t=−2.92) — no case for capital.
- grid was **charged** its trigger move (it buys into a fall): −7.5 → +3.0
  gross, and the "reliably worse than chance, t≈−5.9" finding below is
  **withdrawn** — it was the entry rule. grid now leads both nulls at t≈2 but
  sits at +3 bps gross against a 15 bps round trip: still not `pays`, and not
  BHY-significant at m≈14.
- No row changes status: nothing beats a null after correction, every `status`
  is still `unproven`. The paper wallet was never sized on these numbers
  (nothing was `confirmed`), so there is no realised-PnL consequence to undo.

## Carry evidence — realised episodes, not simulation (since 2026-10-09)

Carries (`CARRY_SIDES`: delta_neutral, inverse_carry, xexch_carry) were never
measured: `_load_candidates` reads only long/short. A carry could therefore
never reach `confirmed`, so `paper_trade._promotion_confirmed` was always False
for it, the book-priced carry's $500/leg ceiling never lifted and Kelly never
applied. A bracket replay is the wrong model for a carry (its PnL is funding,
four fees, two book walks and borrow), so carries get their own evidence path
feeding the **same** `status` field:

- `carry_edge_rows`: one row per carry (strategy, market) from its **closed
  paper positions** over `MATRIX_EDGE_CARRY_DAYS` (90 d; the band's `since`
  when the strategy has a shadow band). Shadow-wallet fills count — a carry's
  paper book is its evidence. Positions are grouped into episodes
  (`shadow_tracker.decompose` → `episode_groups`); the sample is each
  episode's realised net bps (`pnl_usd` is already net of funding, fees, book
  and borrow; `funding_bps`/`borrow_bps`/`book_bps` decompose it). Open
  episodes are `n_open`, not evidence.
- The null is **zero**. `t` is clustered by the UTC day the episode opened
  (same-day carries share one funding regime); with fewer than 20 day clusters
  (`MATRIX_EDGE_CARRY_MIN_DAYS`) `t`=0, p=1 — neither `pays` nor `harmful` can
  be read off a handful of regimes (`t_day` keeps the raw value).
- The row then goes through `apply_promotion_bar` with the directional rows:
  BHY over the whole family (carry pairs now count in `_family_size`), deflated
  Sharpe on the per-episode nets, the pre-registered `required_n` (floor 200)
  and `promotion.status`. `net_of_costs: true` tells `verdict` not to charge
  the round trip a second time.
- Zero-edge check (`tests/test_carry_evidence.py`): 200 synthetic zero-edge
  carries, 300 episodes over 60 days with a shared day shock — **0 confirmed,
  0 `pays`** at m=13; ≤ 5 % confirm even at m=1. With per-day correlation an
  i.i.d. t rejects a true zero in >15 % of runs; the day-clustered t stays
  under 10 %. A +80 bps carry on the same noise confirms.
- Live, 2026-10-09: cash_and_carry 57 episodes / 9 days −3.1 bps,
  inverse_carry 22 / 13 days +11.9, xexch_funding_arb 13 / 6 days −20.3
  (t_day −3.4), neg_funding_carry 0 closed / 3 open — all `unproven`, all
  short of 20 day clusters. Nothing is sized on them yet.

~~Known gap: `paper_trade._kelly_fractions` subtracts `execution_cost_bps × 2`
from the edge before Kelly, double-counting ~15 bps on a carry row.~~ **Fixed
2026-10-09 in 3af1eee**: Kelly does not charge the round trip again on a
`net_of_costs` row.

## Correction — the momentum_xs edge was pseudo-replication (2026-10-09)

The +31…+36 bps / t≈6 claims below are **withdrawn**. 1 764 of the 2 037
non-shadow momentum_xs predictions in the 14-day window came from **one
three-hour burst** on 2026-09-13 16:54–19:40 UTC, in which strategy v1
re-emitted the same ten (symbol, side) pairs every ~90 s (UAIUSDT short 294×,
LSKUSDT long 294×, CVCUSDT long 294×, …). The evenly spaced 800-row subsample
was 87 % that burst. Those rows were scored as independent trades.

| measure (momentum_xs, crypto, gross, vs random time) | n | treat | edge | t |
|---|---|---|---|---|
| old study, rows to 09-20 (rerun 2026-10-09) | 800 rows | +30.5 | +27.3 | +4.52 |
| same rows, (symbol,hour)-cluster-robust | 132 clusters | | −28.7 | −1.97 |
| v1 non-shadow, the 09-13 burst only | 1 764 rows | +39.6 | +37.2 | (≈10 bets) |
| v2 non-shadow, all of 09-13…09-21 | 420 | −12.7 | −18.1 | −1.94 |
| **fixed study, 14 d to 09-20** | **285 episodes** (2 037 rows) | −2.8 | **−10.7** | **−0.98** |
| fixed study, 09-14…09-21 | 393 | −6.1 | −9.3 | −0.94 |
| fixed study, 09-21 only | 151 | −21.6 | −22.4 | −1.35 |

Every momentum_xs version after v1 measured negative against random entry.
There was never an edge for the wallet to capture; the burst manufactured it.

### Where the ~67 bps went — the 155 positions closed 2026-09-21

All 155 closed momentum_xs positions "since 2026-09-21" were opened that one
day (90 in `default` from v2, 65 in `shadow` from v4; nothing traded after).
Wallet: −$91.85 on $29.9k = **−30.7 bps**, 40 % winners. Per-trade means,
bps, chained from the claim to the wallet:

| step | bps | cumulative |
|---|---|---|
| claimed signal edge (old study) | +36.0 | +36.0 |
| (e)+(units) burst removed / regime: the same simulator on these 155 signals, gross level | −62.2 | **−26.2** |
| ↳ of which edge vs random entry on these 155 | −31.9 (control +5.7) | |
| ↳ (d) selection: filled vs unfilled signals that day (−26.2 vs −5.9 gross) | ≈ −8 (t≈−1, noise) | |
| look-ahead in the old entry bar | +1.7 | −24.5 |
| (a) latency 4.0 s, raw fill drift vs `entry_price_ref` −0.7; horizon anchored at `close_by` | −5.8 | −30.3 |
| (b) exit path: tick-sampled TP/SL + fill at mark vs bar high/low bracket | +13.5 | −16.7 |
| (c) costs, 2 × 6.95 bps (taker + measured slippage) | −13.9 | −30.6 |
| funding | −0.04 | **−30.7** = wallet |

So, on the same signals, simulator and wallet differ by **only 4.5 bps**
(−26.2 gross sim vs −30.7 net wallet): execution passed the signal through
almost exactly, costs included. The whole gap is (e): the claimed edge did not
exist outside the burst.

Exit detail (b): 106 horizon exits averaged −5.3 raw; 31 bar-agreed stop-outs
−278 (bracket −267, ≈11 bps stop overshoot); 16 take-profits +326 (bracket
+300). Tick monitoring missed 11 bar-wick touches, which happened to net
positive here; it is not a reliable source of edge.

### MFE / MAE — is the horizon wrong, or the entry?
Within the horizon from the raw fill: MFE mean 181 / median 115 bps, MAE mean
245 / median 149 bps, against TP ≈ 299 and SL ≈ 263. MFE reached TP in 28/155,
MAE reached SL in 41/155. The horizon exits had MFE median 111 and ended at
−2.5 raw. Adverse excursion dominates favourable: **the entry, not the
horizon**. A 300 bps TP is out of reach for a 60–90 min hold (median MFE
115), but moving it in would only harvest noise from an entry with no edge.

## Findings — 30 days to 2026-10-09, episodes, gap guard on

*Measured with the pre-signal entry rule; see the correction above for the
same window under the honest rule.*

Read-only rerun 2026-10-09 12:32 UTC (each strategy alone, registry writes
disabled). `n` = independent episodes simulated, `n_raw` = signal rows,
`unsc` = signals with no clean bars (stale entry or a hole inside the window;
never scored). Edges are gross bps vs the null, t in brackets; `real.` is the
realised net bps per fill from the wallet over the same window. The last column
is the same study before the fix (rows as samples, no gap guard).

| strategy / market | n | n_raw | unsc | gross | vs random time (t) | vs random side (t) | real. | DSR | before: vs time (t), n |
|---|---|---|---|---|---|---|---|---|---|
| funding_reversion / crypto | 1217 | 13804 | 48 | +4.5 | +3.5 (+1.34) | −0.2 (−0.06) | −31.8 | 0.50 | +6.8 (+3.47), 1499 |
| grid / crypto | 1461 | 3538 | 39 | −8.7 | **−8.7 (−5.83)** | **−8.8 (−5.89)** | −17.3 | 0.00 | −0.0 (−0.01), 1499 |
| matrix_agent / crypto | 1054 | 2726 | 217 | +2.7 | +2.2 (+0.82) | +1.9 (+0.70) | −25.6 | 0.23 | +8.3 (+2.71), 1240 |
| matrix_agent / bist | 168 | 393 | 225 | −7.3 | −0.6 (−0.06) | −9.0 (−0.91) | −31.4 | 0.01 | −12.0 (−1.64), 388 |
| matrix_agent / us | 309 | 349 | 40 | −3.0 | −2.9 (−1.38) | −2.6 (−1.25) | −20.2 | 0.00 | −2.7 (−1.14), 349 |
| momentum_xs / crypto | 277 | 2188 | 163 | −18.4 | −23.2 (−2.05) | −18.6 (−1.64) | −34.0 | 0.00 | +24.9 (+5.47), 1496 |
| dca / crypto | 1050 | 1975 | 420 | −0.3 | −0.2 (−0.12) | −1.6 (−0.80) | −32.5 | 0.03 | −5.0 (−2.88), 1470 |
| oi_delta / crypto | 382 | 1395 | 40 | +5.8 | +4.1 (+0.53) | +2.8 (+0.35) | −4.8 | 0.16 | −6.5 (−1.78), 1366 |
| oi_breakout / crypto | 188 | 730 | 25 | −7.3 | −6.7 (−0.49) | −9.8 (−0.71) | −28.1 | 0.01 | −6.6 (−1.04), 698 |
| bist_volume_breakout / bist | 202 | 716 | 132 | −13.2 | −10.7 (−1.79) | −13.5 (−2.25) | −110.7 | 0.00 | +104.0 (+16.71), 716 |
| bist_gap_fade / bist | 109 | 502 | 393 | +15.2 | +22.1 (+2.36) | +15.6 (+1.67) | −41.2 | 0.47 | −53.3 (−8.54), 501 |
| bist_intraday_reversion / bist | 56 | 243 | 45 | +25.0 | +30.2 (+1.58) | +26.4 (+1.40) | −85.2 | 0.35 | +12.9 (+1.52), 243 |
| bist_news_event / bist | 11 | 138 | 35 | +2.9 | +12.0 (+1.21) | +8.2 (+2.29) | — | — | −4.8 (−0.93), 138 |

**Gap guard, bist_volume_breakout.** Rows, no guard: +104.0 bps, t=16.7 on
716 rows. Episodes, no guard: +116 bps, t≈10 — 244 of its entries sat on a
stale bar across a data hole and were all take-profits at +300, while the 281
fresh ones were −14. Episodes with the guard: **−10.7 bps, t=−1.79** on 202
episodes (132 unscorable). The whole "edge" was the jump across the hole.

Reading:
- **Nothing beats a null after correction** (none BHY-significant positive,
  every DSR ≤ 0.50); every `status` is `unproven`.
- ~~**grid is reliably worse than chance**: −8.7 bps against both nulls with
  t≈−5.9 on 1 461 episodes.~~ **Withdrawn 2026-10-09**: the entry rule charged
  grid the fall that triggered each buy; with the honest entry it is +3.0
  gross, +2.9 vs time (t=1.93). See "Correction — pre-signal path".
- funding_reversion's 13 804 rows were 1 217 bets (11×); its t fell 3.47 →
  1.34 and its realised net is −31.8 bps a fill.
- The BIST positives (gap_fade +22, intraday_reversion +30) sit on 56–109
  episodes with 40–80 % of signals unscorable and realised net −41/−85 bps;
  BIST is paused (see [[market-cadence-study]]).

## Findings — 14 days to 2026-09-20, 5 277 signals, both nulls

*Superseded by the 30-day table above. Rows-as-samples, before the episode
fix; momentum_xs line withdrawn above. Other strategies' t-statistics are also
inflated wherever they re-emit.*

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
- ~~**`momentum_xs` has the strongest signal in the book** (+36.0 bps over
  random).~~ **Withdrawn 2026-10-09**: pseudo-replication of one three-hour v1
  burst; as episodes it is −10.7 bps (t=−0.98). Its fills were bad because its
  signal was bad, not because execution lost a real edge (see Correction).
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
- ~~**`oi_breakout` and `dca` pick direction worse than a coin flip**
  (−14.9 t=−2.46, −5.8 t=−2.56): inverted, not mistuned.~~ **Superseded
  2026-10-09** (rows-as-samples, 6dcc8f1; pre-signal entry, cda6ee6): on
  episodes with the honest entry, vs random side oi_breakout −6.7 (t=−0.50),
  dca −0.5 (t=−0.23) — no direction skill either way. `grid` and
  `funding_reversion` beat neither null and produce most of the volume. Volume without edge is
  the cost engine described in [[pnl-reality]].
- **Multiple testing is corrected, not hand-waved.** Since 2026-09-20 the
  report applies Benjamini-Hochberg at FDR 5 % across all 13 simultaneous
  comparisons: **2 of 13 survive** — `momentum_xs` (p<0.0001; withdrawn
  2026-10-09, FDR cannot correct a sample that counts one bet 294 times) and
  `bist_news_event`, the latter on n=33, which is too thin to allocate against.
  See [[methods]].
- **Remaining caveats.** Controls are drawn from the same period, so market
  drift appears in both arms. The simulator enters at a 1m bar open and does
  not model slippage beyond the flat cost assumption.

## How it is used
`make edge-report [DAYS=14] [STRATEGY=x] [DRAWS=20]` prints the table. The slot
pass consults the same study through `_entry_edge_verdict` (cached 6 h):
`pays` exempts a strategy from the realised-loss demotion, `harmful` pulls one
from the book even when its realised PnL looks survivable. See
[[learning-loop]].

`verdict` = `pays` additionally needs the treatment's **gross level** above
the round trip (since 2026-10-09): the wallet is paid the level, not the lead
over a null, and a strategy can beat a losing control by more than the cost
while still losing on every trade.

## Open questions
- ~~Does `momentum_xs`'s +36 bps survive now that fills are fresh?~~ Answered
  2026-10-09: there was no +36 bps; see Correction.
- ~~How much of the remaining gap is slippage the simulator does not model?~~
  On momentum_xs 2026-09-21: sim gross −26.2 vs wallet net −30.7; costs
  −13.9, tick-vs-bar exits +13.5, entry/horizon anchoring −4.1. The simulator
  minus the modelled round trip predicts the wallet within ~5 bps.
- ~~`barrier_study`, `execution_study` and `meta_label` do not yet collapse
  re-emissions.~~ Done 2026-10-09 (6dcc8f1), as are the certificate,
  efficacy, slot scorer, lab fitness, universe symbol edge, the Director
  digest and the dashboard. The last row-counting consumers moved to episodes
  the same day: lessons (5f40b1d), reflection `metrics_window` (746b067),
  setup memory and `load_pair_edges` (1a2ff96), notify's 24 h win rate
  (c592b73). The deferred bulletin service was not audited.
- Would the cost-engine strategies become positive at a much higher signal
  threshold (fewer, better trades), or is their signal empty at every threshold?

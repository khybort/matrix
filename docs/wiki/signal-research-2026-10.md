---
title: Signal research 2026-10 — one carry survives, seven families do not
updated: 2026-10-09
sources: ["services/backtest/research/signal_2026_10/ (fetch, research, bar, PREREG.txt)", "Bybit public API 2025-10-01..2026-10-09: funding settlements, 1h klines, 1h premium index, 1h OI (top 200)", "Bybit /v5/spot-margin-trade/data and Binance bapi margin vip/spec list-all, read 2026-10-09", "matrix_shared.edge_study.benjamini_yekutieli, matrix_shared.promotion.deflated_sharpe"]
status: current
---

After [[strategy-scoreboard]] found no strategy in the book clearing cost, this
study looked for a *new* signal family, outside the book, on a year of public
Bybit history rather than our own 4 weeks of ticker data.

## Design (pre-registered before any return was computed)
- **Data**: every USDT linear perp trading on 2026-10-09 (791): funding
  settlements, 1h klines, 1h premium index; 1h open interest for the top 200.
  Survivorship: delisted coins are absent, which flatters long legs.
- **Split by time**: train 2025-10-01..2026-05-31, holdout
  2026-06-01..2026-10-09. Holdout evaluated once, only for train passers, with
  the rule frozen. Timestamped record: `PREREG.txt`.
- **Universe**: top 100 by trailing-7d turnover, listed ≥ 30 days. Directional
  entries skip one full 1h bar after the decision.
- **Costs**: directional 15 bps per round trip charged on actual turnover,
  funding booked at each settlement held. Carry: 30 bps (four legs) + borrow.
- **Pass**: train net > 0 with t ≥ 2; holdout net > 0, one-sided p < 0.05/k.
- **m = 38 hypotheses** (31 of them seasonality cells), the count used for
  BY and the deflated Sharpe.

## Results (net bps per period; period = episode for H1, rebalance otherwise)
| id | hypothesis | train net | t | n | holdout net | t | n | verdict |
|---|---|---|---|---|---|---|---|---|
| **H1** | inverse carry after a settlement ≤ −0.08 %, 48 h, borrowable coins, borrow ×1 | **+104.2** | **20.0** | 3 464 | **+135.0** | **10.2** | 1 105 | **survives** (BY pass, DSR 1.00) |
| H1 | same, borrow ×3 (stress) | +49.6 | 9.4 | 3 464 | +65.0 | 4.9 | 1 105 | sensitivity |
| H1 | same, flip exit at first non-negative settlement | +82.1 | 17.9 | 3 464 | +111.5 | 10.3 | 1 105 | sensitivity |
| H1 | same, fees 60 bps instead of 30 | +74.2 | 14.3 | 3 464 | +105.0 | 7.9 | 1 105 | sensitivity |
| H2a | funding cross-section L/S deciles, 24 h | +53.6 | 1.16 | 212 | −46.6 | −0.77 | 130 | rejected |
| H2b | same, 72 h | +85.0 | 0.82 | 70 | +66.4 | 0.37 | 43 | rejected |
| H3 | premium-index cross-section, 24 h | +66.0 | 1.47 | 212 | −49.4 | −0.76 | 130 | rejected |
| H4 | OI-conditioned 24 h reversal | −41.0 | −1.39 | 212 | −1.8 | −0.04 | 130 | rejected |
| H5 | 24 h cross-sectional reversal | −37.2 | −0.70 | 212 | +30.7 | 0.42 | 130 | rejected |
| H6 | 7 d momentum (skip 1 d), weekly | +230.4 | 0.88 | 30 | −891.1 | −3.55 | 18 | rejected |
| H7 | day-of-week (7) / hour-of-day (24), top-20 basket | best +131 (Thu short, t=1.73) | | | best +80 (t=0.98) | | | all rejected |
| H8 | large-trade / liquidation imbalance | not tested — needs trade-level history; local `market_trades` has ~17 clean days | | | | | | untested |

H1 numbers are the executable subset: coins with a published borrow rate on
Bybit or Binance cross margin today (3 464 of 4 936 train episodes). Holdout
by month: Jun +141, Jul +138, Aug +114, Sep +169, Oct (9 days) +63 — every
month positive. Day-clustered t: train 17.4, holdout 8.8.

## Claims
- **Hedged negative-funding carry pays; unhedged funding tilts do not.** H2/H3
  collect +120…+300 bps of funding per period, and the price leg gives it all
  back (H2a holdout gross −148): crowded-short coins drift down by about what
  they pay. H1 keeps the funding and drops the drift by shorting spot.
- **The mean is carried by tails.** H1 holdout median +12.7 against a mean of
  +135; the top 5 % of episodes are 57 % of train PnL; trimmed (5 %) mean +90.
  At borrow ×3 the median is −31. This is a persistence-of-squeeze bet with
  positive skew, not a steady coupon. Sizing must assume most episodes are
  flat-to-slightly-negative.
- **Liquid coins earn more, not less.** Holdout by trailing daily turnover:
  < $1M +62 (×3 borrow +5), $1–5M +77 (+4), $5–20M +148 (+72), > $20M +420
  (+337). Deep negative funding on a liquid coin is a large crowded short.
- **inverse_carry's own signals were mostly untradeable.** Of its 2026-09
  symbols, LSK, RARE, SAGA, SOXL, MARSCOIN, AKE, RLC, ZEC, CAP, STEEM, CVC have
  no Bybit spot margin (several no Bybit spot at all); some are borrowable on
  Binance only. A carry that cannot short spot is a naked long perp.
- **Borrow cost assumption**: the codebase charged none. Historical borrow
  rates are not public (Binance's history endpoint needs a signed key), so
  the study charges **today's** VIP0 rate per coin — cheapest of Bybit hourly
  and Binance daily/24 — over the hold (train mean 27 bps/episode), and ×3 as
  stress. On 2026-10-09, coins with funding ≤ −0.04 % showed borrow of
  1–18 bps/8 h, far below their funding (e.g. SKL −117 bps/8 h funding,
  6.4 bps/8 h borrow).
- **The live universe sees almost none of it.** Only 19 of 1 105 holdout
  episodes are in the 25-symbol active crypto universe (mean +21.7, t=1.46) —
  consistent with the in-DB replay (+14.9, n=25). The edge lives in coins we
  do not ingest.

## Implementation
`neg_funding_carry` (services/strategy, commit 2faba91), config row `shadow`
only: the dispatcher now runs a shadow-only strategy as a challenger on the
shadow wallet, and `reflection.efficacy` cannot cut it over (no champion), so
status comes only from evidence. It enters on the last **settled** rate within
1 h of the settlement, only for coins in the live borrow table (public
endpoints, 1 h cache), and stamps `borrow_rate_hourly`. The paper engine's
carry close charges borrow over the hold. That code was a working-tree hunk
here, and it landed as the carry family (7d7b854). Borrow is now charged from
the recorded rate series (32dcadb), and the entry filter prices borrow from
funding depth (13eb0af); see "Borrow measurement" below.

## Open questions
- **Borrow availability** is the binding unknown: a listed rate is not a
  lendable pool, and squeezed coins are exactly the ones whose pools run dry.
  Settles only with a signed account query at signal time (Phase 5).
- **Coverage**: the edge is outside the 25-symbol universe. Since 2026-10-09
  ingestion streams a carry watchlist (borrowable perps at ≤ −0.05 %, cap 20,
  with their spot legs; [[operations]] "Crypto universe"); replayed over the
  holdout it would have covered 1 103 of 1 105 episodes (~59/week, ~50/week
  in the last 30 days) against ~5 a month before. ~~Pending: the dispatcher must
  hand the watchlist to `neg_funding_carry`.~~ Done 2026-10-09 in c49f08b
  (only this module gets the watchlist).
- Four-leg cost on illiquid spot may exceed 30 bps; the 60 bps sensitivity
  still holds (+105, t=7.9 holdout), the < $1M bucket does not at ×3 borrow.

## Adversarial check (2026-10-09)
Independent attempt to break H1 on the same data (exec episodes, train
n=3 464, holdout n=1 105), plus spot klines per episode (Bybit spot, else
Binance spot; 4 419 of 4 569 episodes covered), today's order books for every
episode coin, Bybit delisting announcements and the funding history of 145
delisted perps. Scripts lived in the session scratchpad (`h1check/`), not
committed. Mean net bps per episode, train / holdout; t clustered by ISO week
(35 / 19 clusters).

| # | check | train | holdout | effect vs study |
|---|---|---|---|---|
| 0 | study net, borrow ×1, 30 bps fees | +104.2 (t_wk 10.7) | +135.0 (t_wk 8.1) | — |
| 1 | sign / interval: long perp receives −rate, each raw settlement summed once; 6/12/24/48 settlements per 48 h match 8/4/2/1 h intervals; interval switches mid-episode are handled because raw rates are summed (21 % of episodes have an entry interval ≠ today's) | no change | no change | 0 |
| 2 | real spot hedge (perp close − spot close, not premium index) **and** entry 1 h after settlement | +99.7 | +134.4 | −4 / −1 |
| 3 | funding on mark-to-market notional | +108.9 | +131.7 | +9 / −3 |
| 4 | 4 taker legs (31 bps) + spread/impact from today's books, **$500** per leg (median cost 64 bps vs 30) | **+72.7** (t_wk 7.5) | **+93.4** (t_wk 6.0) | −36 / −38 |
| 5 | same at **$5k** per leg (median cost 139 bps) | −48.1 | +1.8 (t 0.1) | −157 / −130 |
| 6 | row 4, borrow ×3 | +18.1 (t_wk 2.0) | +23.5 (t_wk 1.5) | −55 / −70 |
| 7 | row 4, borrow ×10 | −173.0 | −221.4 | −246 / −315 |
| 8 | row 4, top 5 % of episodes removed | +9.3 (t_wk 2.2) | +19.8 (t_wk 3.7) | −63 / −74 |
| 9 | row 7 (×10 borrow), top 5 % removed | −237.1 | −295.6 | — |
| 10 | row 4, only episodes with 48 h funding ≤ 500 bps (94 % of them) | +5.5 (t_wk 1.5) | +4.7 (t_wk 0.8) | −67 / −89 |
| 11 | clustering: study net, t by day / by week | t 12.6 / 10.7 | t 9.8 / 8.1 | t only |
| 12 | survivorship: 145 delisted perps add 1 467 train / 130 holdout episodes; their funding leg is similar (+111 / +151 mean); 42 straddle the delisting | bound ≈ −15 | bound ≈ −10 | 0 central, −15 pessimistic |

Findings:
- **The accounting is right.** Funding sign, per-settlement counting and the
  1/2/4/8 h intervals are correct; the premium-index basis term matches a
  real spot-hedged P&L (17 vs 21 bps mean, corr 0.77); perp < spot at 77 % of
  entries and the basis *converges in our favour*; entering one hour later
  costs nothing. No look-ahead found (rate known at settlement, premium bar
  ends at settlement). Not an artefact of the three kinds withdrawn today.
- **The magnitude is a cost-and-borrow artefact.** Spread is real money on
  these coins: median spot turnover of an episode coin is $0.23 M/day (perp
  $4.4 M), round-trip impact at $5k is ~40 bps per leg. At $5k per leg the edge
  is zero.
- **It lives where borrow is least believable.** Top-5 % episodes collect a
  median 951 / 1 179 bps of funding in 48 h, 36–40× the borrow charged. That
  gap staying open for two days is itself evidence that borrow was not
  available at that rate — anyone with borrow would have closed it. Today's
  snapshot agrees: coins now at funding ≤ −8 bps/8 h pay median 6.2 bps/8 h to
  borrow, against 0.4–1.0 for coins near zero funding — so charging a calm
  coin today's rate for its squeeze understates by several ×. Without the
  episodes paying > 500 bps in 48 h, the net is +5 bps, t < 1.6.
- **Concentration**: top 5 % of episodes are 88 % (train) / 79 % (holdout) of
  the $500-cost PnL; top 10 days are 21 % / 42 %; holdout's top five coins (H,
  HOME, ONG, LSK, T) are 44 %. Concurrency is low except 2025-10-11 (163
  episodes in one day, mean −36).
- **Survivorship is not the problem.** Delisted coins had similar funding;
  the bias is rather that "borrowable today" silently drops coins whose
  lending was discontinued and adds coins whose margin arrived later — it
  cannot be measured from public data.

**Verdict: (b) real but much smaller, and not sized.** Best net estimate:
**about +20 bps per episode at ≤ $500 per leg with borrow at ~3× today's
rate** (week-clustered t 1.5–2.0, CI includes zero), **≈ 0 at $5k**, negative
if squeeze borrow is ≥ 6× today's. The study's +104 / +135 bps assumes free
spread and calm-day borrow on squeezed coins. Consequences: no capital
allocation from this evidence; the `neg_funding_carry` shadow run is still
worth keeping because the decisive unknown — borrow you can actually get, at
what rate, at signal time on a squeezed coin — is only measured by a signed
borrow quote at entry (record quoted rate and max loanable per signal), and
the strategy should skip any episode whose quoted borrow × 48 h + measured
book cost exceeds a third of the funding it expects.

## Shadow-book hardening (2026-10-09)
Goal: the `neg_funding_carry` shadow book should earn what real money would
earn, so its eventual promotion status means something.

**Entry filter** (strategy, at signal time). Per signal:
- expected = |settled rate| × settlements in 48 h at the interval in force
  (from the gap to `next_funding_ts`: 1/2/4/8 h);
- borrow = quoted hourly rate (cheapest of Bybit/Binance public tables) × 48 ×
  `MATRIX_CARRY_BORROW_STRESS` (3);
- book = 4 taker fees (perp 5.5 + spot 10 bps, ×2 = 31) + all four walked
  fills (perp buy/sell, spot sell/buy, against mid) at the intended per-leg
  size. Spot leg = the borrow venue's `<COIN>USDT`. Books come from
  `market_orderbook_snapshots` if ≤ 30 s old, else public REST depth;
- per-leg size = largest notional at which each of the four walks stays within
  `MATRIX_CARRY_MAX_LEG_IMPACT_BPS` (10) of mid, capped at $500
  (`MATRIX_CARRY_UNCONFIRMED_MAX_LEG_USD`); under $50 is a skip;
- **skip when borrow + book > expected × `MATRIX_NFC_MAX_COST_SHARE` (1/3)**, or
  when either book is missing (no spot book = no hedge = no signal).
All of it is recorded in `predictions.context` (`entry_filter`, `spot_venue`,
`spot_symbol`, `leg_cap_usd`, `book_sources`).

**Paper accounting** (`backtest.carry_books`, used by `paper_trade` for any
carry whose context names a spot leg). At open the engine fetches both books
again, refuses the position if either is missing, and lowers the size to the
impact cap and the $500 ceiling. The ceiling lifts only when
`edge_study.strategy_edge` reports promotion status `confirmed`; unknown counts
as not confirmed. The wallet risk gate stays the upper bound in every case.
The two opening walks are stamped as `context.book_open` in the same
transaction as the position. At close it charges the four fees, the recorded
opening walks and the closing walks on the books at close (the open-time
estimate if a book is gone; `book_close.close_source` says which), plus
borrow at the entry quote × stress for every started hour
(`borrow_charged_usd`). Other carries keep the flat two-leg cost.

**Replay** of the filter over the study's exec episodes (the same episodes as
`services/backtest/research/signal_2026_10/`, with the adversarial check's
`h1check` columns). Books are full public depth for every episode coin, fetched
2026-10-09 13:28 UTC and applied to all past episodes, as in the check. Net =
funding on MTM notional + real spot hedge, entry 1 h after settlement, minus
the book-walked cost at the book-capped size, minus borrow at the stated
multiple of today's quote. t clustered by ISO week.

| set | train n | train net bps (t_wk) | holdout n | holdout net bps (t_wk) |
|---|---|---|---|---|
| all exec, flat $500, borrow ×3 (= check row 6, today's books) | 3 370 | +19.3 (2.1) | 1 059 | +20.3 (1.2) |
| all exec, book-capped size, borrow ×3 | 3 103 | +34.7 (3.5) | 972 | +34.6 (1.9) |
| **filter kept, borrow ×3** | **1 081** | **+175.6 (6.7)**, median +12.7 | **221** | **+292.5 (6.3)**, median +96.8 |
| filter kept, borrow ×1 | 1 081 | +216.9 (7.7) | 221 | +341.0 (7.2) |
| filter kept, borrow ×10 | 1 081 | +30.9 (1.5) | 221 | +122.7 (2.6) |
| filter kept, ×3, top 5 % removed | 1 026 | +87.3 (5.2) | 209 | +177.5 (6.7) |
| filter kept, ×3, 48 h funding ≤ 500 bps | 931 | +29.4 (2.7) | 172 | +72.3 (4.7) |

Drops, train / holdout: no book today 361 / 133 (mostly delisted or no spot
pair), impact cap < $50 162 / 38, cost share > 1/3 1 860 / 713. Kept legs
average $355 / $336 (median ≈ $400: the $500 ceiling rarely binds before the
10 bps cap does). That is about **$6 / $10 per episode**, 136 / 52 episodes a
month across 217 / 89 coins, if ingestion covers every borrowable perp.
Win rate 53 % / 66 %. Concentration is lower than in the unfiltered set but
still high: top 5 % of kept episodes are 52 % / 41 % of their PnL, the top 5
coins 28 % / 46 % (holdout: H, LSK, COTI, HOME, SKR), and the top 10 days
24 % / 47 %.

How to read it:
- **The filter works by selecting deep funding, not by modelling costs.**
  Keeping the same number of episodes ranked by expected funding alone gives
  +167 / +291 (overlap 925 / 170). Expected funding at the entry rate overstates
  what is realised by 4–7× (median realised / expected 0.15 train, 0.25
  holdout: rates decay fast). So the 1/3 rule is lenient in practice. Against a
  decay-adjusted expectation (about share ≤ 0.08 of the naive one) it keeps
  190 / 22 episodes at +354 / +929 bps (borrow ×3) and +235 / +787 at ×10.
- **Borrow is still the open risk, and the replay understates it.** The
  replay charges today's calm quote. Live, the filter reads the quote at signal
  time, which for a coin in a squeeze is several times higher (median
  6.2 bps/8 h at funding ≤ −8 bps/8 h). The live filter will therefore keep
  fewer episodes than the replay. At ×10 borrow the train set is not
  significant (t 1.5). Quoted ≠ lendable: a missing pool is still invisible
  without a signed query.
- **What the shadow book should show if the edge is real:** roughly +100 to
  +300 bps per kept episode net of everything (median far below the mean,
  a third to half of episodes losing a little), at ~$350 per leg, with PnL arriving
  in a few squeezes. A shadow mean near +30 bps or below means the quoted
  borrow, not the funding, was the gap. Judging needs the promotion bar's
  registered n, not a month of results. Replay scripts: session scratchpad
  `shb/` (`fetch_books.py`, `replay.py`), not committed.

## Round 2 (ticks) — 2026-10-09: 29 cells, nothing survives
Goal: a signal whose **gross** edge per episode is ≥ 45 bps (3× a taker
round trip), on data round 1 could not see: every print with its aggressor
side. Scripts, pre-registration and its timestamped log:
`services/backtest/research/signal_2026_10_r2/` (`PREREG.txt` committed in
cdb5b8b before any return was computed).

**Data.**
- **Tick panel**: Bybit public trade archive for 40 crypto perps, the top 40
  by Bybit turnover in April 2026, listed before March. 2026-04-24..10-08,
  6 720 symbol-days, ~120 GB gz streamed and reduced to 1-minute bars
  (aggressor USD per side, USD in prints ≥ $10k/50k/250k/1M, spread proxy).
- **Binance** USD-M 1m klines for the same 40 (H10).
- **Kline tests**: round 1's year of 1h klines, premium, funding and OI, plus
  Bybit 5m klines around 30 367 extreme settlements (H9).

**Rules.** Tick tests: train May–Jul, holdout Aug–Oct 8. Kline tests use
round 1's split. Entry one full bar after the signal bar. Cost = 2 × 5.5 bps
taker + measured spread:
- *tick tests*: the per-minute spread proxy (last buy print vs last sell
  print, ≤ 10 s old), which overstates the touch;
- *kline tests*: a turnover map fitted on 7 137 tick symbol-days, log s =
  4.53 − 0.213 log turnover (4.9 bps at $1M/day, 2.6 at $20M).

Funding is charged at every settlement crossed. One episode per (cell,
symbol) at a time. t is day-clustered. Pass: train net > 0 with t ≥ 2, then
holdout net > 0 with t ≥ 2. To be implemented, a cell also needed holdout
p < 0.05/k and net ≥ +15.

| cell | rule (short) | train gross | train net (t_day) | n | holdout net (t_day) | n | verdict |
|---|---|---|---|---|---|---|---|
| H8a-flow follow 60m / 240m | 15-min aggressor imbalance ≥ 0.35 at ≥ 4× normal volume | −3.0 / −11.5 | −17.8 (−6.8) / −26.2 (−5.7) | 2 812 / 2 269 | −13.0 / −9.7 | 2 678 / 2 055 | rejected |
| H8a-flow fade 60m / 240m | same, opposite side | +3.0 / +11.5 | −11.7 (−4.5) / −3.1 (−0.7) | 2 812 / 2 269 | −19.0 / −22.0 | | rejected |
| H8a-large follow 60m / 240m | 15-min net large-print USD ≥ 5σ (7 d) | −3.3 / −1.9 | −17.8 (−5.0) / −16.2 (−3.1) | 3 012 / 2 461 | −17.7 / −17.4 | 2 471 / 1 922 | rejected |
| H8a-large fade 60m / 240m | same, opposite side | +3.3 / +1.9 | −11.3 (−3.1) / −12.4 (−2.4) | | −13.0 / −12.9 | | rejected |
| H8b-1m fade 30/60/240m | 1-min move ≥ max(1 %, 6σ), ≥ 70 % same-side aggressor, ≥ 8× volume | +25 / +27 / +28 | +8.2 (1.6) / +9.5 (1.2) / +11.4 (1.0) | 821 / 796 / 735 | −23.3 / −10.3 / −8.5 | 450 / 435 / 400 | rejected |
| **H8b-5m fade 30m** | 5-min move ≥ max(2 %, 5σ), ≥ 65 % same-side, ≥ 5× volume | +43.2 | **+25.8 (2.54)** | 441 | **+9.4 (0.64)** | 443 | train pass, **holdout fail** |
| **H8b-5m fade 60m** | same | +49.2 | **+32.0 (2.57)** | 436 | **+18.1 (1.05)** | 430 | train pass, **holdout fail** |
| **H8b-5m fade 240m** | same | +65.8 | **+49.0 (2.85)** | 416 | **−2.0 (−0.09)** | 398 | train pass, **holdout fail** |
| H10 15m / 60m | Binance − Bybit 5-min return gap ≥ 30 bps, trade Bybit toward Binance | +7.3 / +1.2 | −10.5 (−3.9) / −16.1 (−2.5) | 1 496 / 1 165 | −20.8 / −10.3 | 2 060 / 1 511 | rejected |
| H9-pre ≥ 0.10 % / ≥ 0.30 % | 30 min into settlement, against the payer of the predicted rate | +3.8 / +4.0 | −10.1 (−4.8) / −9.8 (−2.2) | 19 098 / 6 015 | −15.0 / −7.3 | 6 484 / 2 450 | rejected |
| H9-post ≥ 0.10 %, 30m / 240m | after settlement, reversal (long if rate > 0) | +6.7 / +47.2 | −7.1 (−2.4) / −8.9 (−1.0) | 16 123 / 10 478 | −3.2 / −4.1 | | rejected |
| H9-post ≥ 0.30 %, 30m / 240m | same | +13.7 / +104.6 | −0.0 (0.0) / +6.5 (0.4) | 5 468 / 3 533 | +4.3 / +12.2 | 2 204 / 1 473 | rejected |
| H11 build-up 4h / 24h | 4h OI ≥ +15 % and \|move\| ≥ 3 %, follow | −36 / −68 | −19.6 (−0.7) / +6.2 (0.1) | 758 / 597 | −102 / +92 (0.6) | 358 / 280 | rejected |
| H11 flush 4h / 24h | 4h OI ≤ −15 % and \|move\| ≥ 3 %, fade | +200 / +88 | +203 (1.84) / +141 (1.78) | 386 / 324 | +144 (1.93) / +172 (0.95) | 125 / 111 | rejected (near miss) |
| H11c flush 4h, 2024-10..2025-09 | frozen rule, earlier unseen year (addendum) | +73.2 | **+61.1 (0.94)**, median +11.6 | 364 | — | | **fails confirmation** |
| H12 new-listing short 7d / 14d | short at listing + 25 h | −1 751 / −4 733 | −1 987 (−0.8) / −5 290 (−0.9) | 81 / 81 | −694 / −202 | 24 / 24 | rejected |

Holdouts of train failures were computed afterwards, for the record only.
**m = 29.** The three train passers have BY q = 0.20 over the 28 train
p-values. Nothing is implemented.

### Claims
- **Order flow does not predict at 1–4 h on liquid perps; it costs.** Every
  imbalance and large-print cell is ≤ ±12 bps gross. Following and fading are
  near mirror images, so the cost (~15 bps) decides the sign. The informative
  part of aggressor flow is spent within minutes, which is the maker study's
  adverse selection seen from the other side ([[maker-execution]]).
- **Cascade bounces are real but not stable.** Fading a 5-minute same-side
  burst made +43…+66 bps gross in train. That cleared 3× cost, but almost all
  of it came in early June: the top 10 days were 94–100 % of train PnL, and
  long-after-dump beat short-after-pump. In the holdout it made +17…+37 gross
  and 18–19 bps of cost (spreads are wider at these moments than on an
  average day). Net was +9 / +18 / −2 at t ≤ 1.05. The 1-minute version
  never cleared cost.
- **The post-settlement rebound is paid back in funding.** After a settlement
  ≥ 0.30 %, going long against the payers earns +105 bps gross over 4 h
  (holdout +122). The rate persists, though, and pays −84 / −96 bps over the
  same hold. That is round 1's H2 result at intraday scale: price drift and
  funding offset each other.
- **Pre-settlement positioning is not exploitable**: +4 bps gross.
- **Cross-venue lead-lag is a seconds effect.** A 30 bps Binance–Bybit gap
  closes for +1…+10 bps on Bybit after a minute's delay, which does not pay
  the cost.
- **OI flush + fade is the closest miss, and it fails out of sample.**
  It was positive in train (+203) and holdout (+144), both at t < 2. On a
  fresh earlier year it gave +61, t = 0.94, median +12, with the top 5 % of
  episodes carrying ~100 % of the PnL. Its median falls from +118 to +43 to
  +12 across the three periods.
- **New-listing shorts are positive in the median and ruinous in the mean**
  (median +1 248 bps at 7 d; one coin, COAI, went up 20× in a week). With no
  stop or cap, the tail decides the result.

### Survivorship and other limits
- Every source lists perps trading on 2026-10-09. The panel was chosen by
  April turnover among today's coins. Delisted coins are absent, which hurts
  H8b (crashed coins bounce less) and H12 most. H11c is worse: only 130 of
  the 200 symbols existed in 2024-10..2025-09.
- The spread proxy comes from prints, not quotes, and overstates the touch.
  The kline spread map uses a typical day's spread, but events happen at wide
  moments, so H9/H11/H12 costs are understated by a few bps. That would not
  rescue any cell.
- No historical liquidation feed exists (Binance `liquidationSnapshot`
  returns 404), so H8b is a proxy based on aggressor bursts.
- Data kept in the session scratchpad (`r2/`): minute bars (346 MB
  compressed), the episode tables, the 5m windows and the spread map. Raw
  ticks were deleted (14.0 GB freed, plus ~120 GB streamed and dropped
  during reduction).

### What would reopen it
H8b-5m and H11-flush are both tail bets on forced flows. Each could only be
reopened with a **new pre-registered test on new data**: for H8b, the live
`market_trades` stream from 2026-10-09 onward; for H11, a quarter of forward
OI. Re-cutting these periods would just be more searching. The ≥ 45 bps-gross
search came up empty on tick data. The only surviving edge is still round 1's
hedged negative-funding carry.

## Round 3: positive-funding mirror — 2026-10-09: 18 cells, nothing survives
Question: H1 lost most of its edge to borrow realism. Its mirror needs no loan: after an extreme
**positive** settlement, short the perp, buy spot, collect funding. `cash_and_carry` already trades
this, with a low threshold on the 25-coin universe. Scripts, pre-registration and its timestamped log:
`services/backtest/research/signal_2026_10_r3/` (`PREREG.txt` committed alone in 8346dbd before any
return was computed).

**Design.**
- Data: round 1's year of Bybit funding, 1h klines and premium. For the hedge, 1h spot klines
  (Bybit `<COIN>USDT`, else Binance, taking whichever venue has a price at entry) for the whole year.
  Today's full books for every perp/spot pair.
- Signal: a settlement with raw rate ≥ X, X ∈ {0.05, 0.08, 0.15} %. Enter 1 h later, short perp and
  long spot at equal notional.
- Exit: hold 24 / 48 / 96 h, fixed or with a flip exit (leave one hour after the first settlement
  ≤ 0). That gives **18 cells**. One episode per (cell, symbol) at a time. Round 1's split.
- Net = funding at every settlement, on mark-to-market notional + real spot hedge
  (spot return − perp return) − cost. Cost = 4 taker fees (31 bps) + the four fills walked on
  today's books at $500 per leg. No borrow. A pair without a book today is excluded (counted:
  3–112 per cell).
- Pass: train net > 0 with week-clustered t ≥ 2. Then holdout net > 0, t ≥ 2 and BY q ≤ 0.05
  over the whole programme, **m = 38 + 29 + 18 = 85**.

**Result: no cell passes train**, so nothing went to holdout (k = 0). Holdout figures below were
computed afterwards, for the record only. Net is bps per episode; t is clustered by ISO week.
Gross = funding + hedge. Fees-only = gross − 31, a zero-spread bound.

| cell | train net (t_wk) | n | holdout net (t_wk) | n | holdout gross | fees-only holdout | BY q |
|---|---|---|---|---|---|---|---|
| X 0.05 %, 24 h / flip | −75.6 (−15.5) / −62.3 (−19.0) | 595 / 641 | −87.2 (−7.1) / −68.9 (−9.6) | 559 / 585 | −6.4 / +11.8 | −37.4 / −19.2 | 1.0 |
| X 0.05 %, 48 h / flip | −85.4 (−11.3) / −58.3 (−16.2) | 486 / 564 | −134.0 (−3.9) / −60.8 (−7.1) | 469 / 507 | −53.6 / +19.5 | −84.6 / −11.5 | 1.0 |
| X 0.05 %, 96 h / flip | −93.6 (−6.8) / −49.2 (−10.5) | 378 / 501 | −202.3 (−2.6) / −52.4 (−5.5) | 379 / 439 | −122.8 / +27.2 | −153.8 / −3.8 | 1.0 |
| X 0.08 %, 24 h / flip | −84.6 (−8.7) / −66.1 (−9.7) | 280 / 301 | −103.9 (−4.4) / −69.6 (−8.6) | 240 / 258 | −19.0 / +14.6 | −50.0 / −16.4 | 1.0 |
| X 0.08 %, 48 h / flip | −103.8 (−6.8) / −58.0 (−8.0) | 238 / 281 | −190.9 (−2.8) / −60.2 (−6.7) | 207 / 236 | −107.0 / +23.0 | −138.0 / −8.0 | 1.0 |
| X 0.08 %, 96 h / flip | −133.6 (−5.3) / −51.0 (−6.3) | 188 / 258 | −334.8 (−2.0) / −55.0 (−5.6) | 172 / 215 | −251.4 / +27.9 | −282.4 / −3.1 | 1.0 |
| X 0.15 %, 24 h / flip | −115.6 (−3.9) / −70.0 (−4.5) | 82 / 96 | −132.0 (−4.0) / −61.8 (−4.2) | 73 / 79 | −47.0 / +22.2 | −78.0 / −8.8 | 1.0 |
| X 0.15 %, 48 h / flip | −136.3 (−3.3) / −68.3 (−4.1) | 72 / 92 | −179.9 (−3.1) / −51.2 (−3.0) | 63 / 76 | −94.8 / +32.4 | −125.8 / +1.4 | 1.0 |
| X 0.15 %, 96 h / flip | −208.5 (−4.1) / −67.0 (−4.0) | 59 / 90 | −877.7 (−1.7) / −49.0 (−3.0) | 54 / 75 | −794.9 / +34.1 | −825.9 / +3.1 | 1.0 |

The other robustness views all point the same way:
- Excluding the top 5 % of episodes makes every cell 6–60 bps worse.
- Day-clustered t has the same sign everywhere.
- At $5k per leg, cells lose 275–1 145 bps. With fees ×2, every cell is 31 bps worse.
- Holdout months: 0 of 5 are positive in every cell. Train: 0 of 8, except the X 0.15 % cells at
  1 of 8.
- Walked cost at $500 per leg is 77–85 bps per episode (book median 66 bps, p90 95). The hedged
  universe is illiquid spot.

**Reference rows** (not counted in m): `cash_and_carry` **as it actually runs**. Its
`strategy_configs` row v1 has `min_funding 0.0001` and `horizon_s 28800`, and that row overrides the
module's v4 defaults (0.08 %, 48 h) through `strategy.params`. On the 25-coin universe it gives
**−39.4 bps (t_wk −114, n 2 362) in train and −38.9 (t_wk −137, n 1 359) in holdout**. Funding brings
+0.2…+0.6 bps per episode and cost takes 40. That matches the in-DB −31 bps (t −6.3, n 95) in
[[strategy-scoreboard]]. On that universe the v4 defaults, and every round-3 cell, have **zero**
hedgeable episodes in the year. Of the five coins there that reach ≥ 0.05 %, AKE, BTW, SOXL and
USELESS have no USDT spot pair. ZEC reached it once, on 2025-10-02.

### Claims
- **Positive extremes are one-settlement spikes, not a regime.**
  - The median entry rate is only 10.5 bps.
  - Realised funding over the hold has a median of 3–22 bps. A naive "entry rate × settlements"
    forecast says 36–440, so realised is 1–26 % of that forecast. H1 realised a median of
    +47 / +58 bps in 48 h.
  - Even with zero spread, the best cell grosses +34 bps against 31 bps of fees alone.
    Fees-only net is −19…+3.
- **The tail is a flip into a short squeeze, and it is lethal for a fixed hold.**
  - Coins that print a positive spike often squeeze the other way a few hours later. Their funding
    then goes deeply negative while we are short the perp. Examples: B3 −2 461 bps in 48 h (perp
    +122 %), H −7 920 bps, LSK −5 327 bps with the perp up 2 172 % intra-hold.
  - 11–48 % of episodes realise negative funding, against 10–11 % for H1.
  - This is why fixed-hold means fall with hold length (X 0.15 %, 96 h holdout −878, median −84),
    and why the flip exit is the least-bad variant in every row.
- **Premium blowout was not the failure mode; perp run-up is.**
  - The entry basis is small: median 0…+13 bps, and at X 0.15 % the perp actually sits below spot
    (−6…−20 bps).
  - Hedge P&L has a median of 0…+13 bps (corr 0.6 with the entry basis), so the premium converges,
    as in H1.
  - The risk sits in the short perp leg as a separate position. Its p90 run-up within the hold is
    +12…+49 %. 6–28 % of episodes see the perp +20 % and 1–10 % see +50 %. That means liquidation
    at 5× or 2× when the spot hedge sits on another venue or another margin account.
  - The worst mark-to-market within the hold reaches p10 −67…−1 330 bps and p1 −240…−19 000 bps.
    An honest live version needs a cross-margined unified account or very low perp leverage, and
    both add cost the test does not charge.
- **The hedgeable universe is small.** Of 497 perps with a settlement ≥ 0.05 % in the year, only
  180 coins have a USDT spot pair on Bybit or Binance. They cover 17 % of those settlements. Positive
  extremes live mostly in perp-only coins, where "carry" would be a naked short.
- **Why the mirror is not symmetric.** In H1 the payers are crowded shorts in a coin that keeps
  drifting down, and that persistence funds the trade (only the borrow was doubtful). On the positive
  side, Bybit's funding hits mostly the long-squeeze peak. It mean-reverts within one or two
  settlements and often overshoots into a short squeeze.

**Verdict: rejected; the question is closed.** No cell survives, so nothing maps onto
`cash_and_carry`'s parameters. What does follow for the main session, routed to the owner of the
carry code: `cash_and_carry` as configured (0.01 %, 8 h) loses about 39 bps per episode
structurally. Its funding (< 1 bp) can never pay a two-leg cost of about 40 bps. No threshold,
universe or hold on the long-spot side turns it positive on this year of data. Raising it to the v4
defaults on the 25 coins makes it silent, and widening it to the carry watchlist would trade the
negative cells above. Retiring it, or leaving it shadow-only, costs nothing in edge.

A fetch bug was caught before the write-up. Bybit's spot kline endpoint returns its newest 1 000
bars at or before `end` even when they predate `start`, so a pair delisted before the window looked
"present". That suppressed the Binance fallback for those coins. After the fix, every number moved
by ≤ 35 bps (most by < 5) and no verdict changed. See the `PREREG.txt` log and `docs/ENGINEERING_LESSONS.md`.

What would reopen it: only a new pre-registered test on forward data. Candidates are a
unified-margin venue where both legs share collateral, plus a maker-only cost model (fees alone
already exceed the median gross). Re-cutting this year would only be more searching. Data stays in
the session scratchpad `r3/`: spot klines 2.08 M rows, books, per-episode tables.

## Round 3b: perp-perp hedge — 2026-10-09: 6 cells, nothing survives
Question: can H1 drop the spot borrow? Instead of shorting spot, short the **same coin's perp on
another venue**, where funding is usually less negative. P&L = Bybit funding received − hedge-venue
funding paid + cross-venue basis change − cost. Scripts, pre-registration and timestamped log:
`services/backtest/research/signal_2026_10_r3b/` (`PREREG.txt` committed alone in e192f10 before
any hedge price, funding or book was fetched).

**Design.**
- Signal: Bybit settlement with raw rate ≤ X, X ∈ {−0.08, −0.15} %, any Bybit USDT perp (as H1).
  Enter 1 h later: long the Bybit perp, short the hedge perp, equal notional.
- Hedge venue at entry: among Binance / OKX / Bitget / Gate, take the one listing the coin with
  the **least negative last settled rate per 8 h** (point in time). Each venue's own settlement
  timestamps and interval; every raw settlement counted once, on mark-to-market notional.
- Exit: fixed 24 h, fixed 48 h, or CONV (first Bybit settlement whose per-8h rate is above the
  hedge venue's, cap 96 h). That gives **6 cells**.
- Cost: 4 perp taker fees at VIP0 (Bybit 5.5; Binance / OKX 5.0; Bitget 6.0; Gate
  max(5.0, contract field), which is 7.5 on some contracts), plus the four fills walked on today's
  books (16:32 UTC) at $500 per leg. Median round-trip walk: Bybit 16.5 bps, Binance 8.7, OKX 11.5,
  Bitget 14.7, Gate 25.6.
- Pass: train net > 0 with week-clustered t ≥ 2. Then holdout net > 0, t ≥ 2, BY q ≤ 0.05 over
  **m = 38 + 29 + 18 + 6 = 91**, and the 3× liquidation-adjusted net > 0.
- **Data limit**: public funding history reaches back to 2025-10 on Binance only. Gate starts
  2026-04-13, OKX 2026-06-29, Bitget 2026-07-11. So train is Binance (91 %) plus some Gate, and the
  holdout is mostly Gate / Bitget. A venue is eligible only where its history exists at entry.
  Coins delisted from Binance during the year are absent (exchangeInfo lists only live symbols).

**Result: no cell passes train**, so nothing went to holdout (k = 0). Holdout figures were
computed afterwards, for the record only. All figures: net bps per episode, as % of one leg's notional.
"Per capital" is return on both margins: net / 2 unlevered, and the liquidation-adjusted net × 1.5
at 3× per leg. t is clustered by ISO week (35 / 19 weeks).

| cell | train net (t_wk) | n | per capital unlev / 3× | holdout net (t_wk) | n | per capital unlev / 3× |
|---|---|---|---|---|---|---|
| X08.F24 | −41.2 (−16.6) | 5 607 | −20.6 / −69.3 | −68.7 (−7.0) | 2 389 | −34.4 / −115.4 |
| X08.F48 | −34.9 (−9.6) | 4 216 | −17.4 / −70.1 | −69.6 (−5.5) | 1 819 | −34.8 / −127.0 |
| X08.CONV | −45.7 (−29.1) | 9 373 | −22.9 / −69.6 | −58.4 (−13.8) | 3 217 | −29.2 / −101.0 |
| X15.F24 | −40.7 (−10.6) | 3 359 | −20.4 / −67.1 | −76.8 (−5.0) | 1 453 | −38.4 / −128.9 |
| X15.F48 | −38.7 (−8.7) | 2 641 | −19.4 / −73.3 | −77.5 (−4.0) | 1 114 | −38.7 / −145.5 |
| X15.CONV | −48.1 (−19.1) | 5 318 | −24.1 / −72.9 | −62.6 (−8.7) | 2 033 | −31.3 / −115.5 |

BY: no train passer, so no new p-values enter the programme. Every cell's holdout p is > 0.999,
and q = 1 for all six at m = 91. Medians are −50 … −66. 13 % of episodes are positive.
Sensitivities, all negative: $5k per leg −103 … −185; fees ×2 −56 … −102; Binance-only reference
−33 … −156; dropping ticker collisions (ON on Binance and H on Gate are different tokens; 18–142
episodes per cell) −34 … −57.

Decomposition, X08.F48 train / holdout (bps per episode): Bybit funding **+179 / +138**, hedge
funding **−155 / −116**, basis −9 / −20, cost 50 / 72. **Gross before cost +15 / +2.**

### Claims
- **The squeeze is cross-venue.** At entry the least-negative venue was at a median −19 bps/8h,
  against Bybit's −37 (train). Over the hold, though, it paid 86 % of what Bybit paid: shorts
  crowd every venue, and the "calm" venue catches up within hours. Only 10 % of train entries
  (37 % of holdout) had a hedge venue at ≥ 0. The entry-time differential does not rank outcomes:
  gross by entry-differential quintile is +1 … +27 train and −82 … +55 holdout, with no monotone
  pattern.
- **What is left is smaller than four taker legs.** The realised differential plus basis comes to
  +3 … +23 bps gross per episode. Four perp legs plus two walks cost 49–51 bps on Binance-heavy
  train and 65–72 on Gate-heavy holdout. Fees alone (~21 bps) take nearly all of it: at zero
  spread the best train cell would be −5 and the best holdout cell +2.
- **Basis risk is real, and it hits the hedge leg.** The cross-venue basis closes against us on
  average (−9 to −37 bps). On closes, the worst basis mark within the hold has a median of −14 to
  −40 and a 1 % tail of −280 to −1 190 bps. The intrabar bound (Bybit low vs hedge high) has a
  median of −400 to −760. Liquidation without top-up: **3×: 3–12 % of episodes; 5×: 7–30 %**. About ¾ of these are
  the **short hedge leg**: the squeeze lifts the price on every venue, and the leg short on the
  other venue is the one that blows up. Per unit of capital deployed, the result is −17 … −39 bps
  unlevered, −67 … −146 at 3× and −127 … −256 at 5×.
- **Consistent with the book.** `xexch_funding_arb` measured −25.6 bps (t = −3.56, n = 40) on 25
  coins at a 0.05 %/8h threshold. Moving it to H1's design (extreme negative funding, wide
  universe, best venue chosen point in time) makes it worse, not better: deeper squeezes bring a
  larger hedge-leg funding bill and more basis and liquidation risk.

### Consequences
- No `xexch_funding_arb` parameter change can turn it into a surviving design; there is nothing to
  route. On its own measured −25.6 (t = −3.56) plus this test, it is a retirement candidate (the
  main session decides).
- H1's edge stays tied to **spot** borrow. A perp short does not replace the borrowed spot leg,
  because the perp short pays the same squeeze funding that the long collects. The binding unknown
  is still borrow availability at signal time.
- Round 3 and round 3b close both borrow-free variants of the negative-funding carry: the
  positive-funding mirror (spot long) and the perp-perp hedge.
- Data stayed in the session scratchpad (`r3b/`): venue funding and klines, `books.pkl`, and the
  episode tables (`episodes_record.pkl`).

## Live path and funding decay (2026-10-09)
**Live path, traced end to end** on the first settlements after the watchlist reached the module
(c49f08b, 14:04 UTC):

| step | KAIA 14:00 settlement (−0.50 %/1 h) | 16:00 settlement: RLC, SAND, ORCA, SKL, API3, UMA, CHR |
|---|---|---|
| emission (settled ≤ −0.08 %, borrowable, ≤ 1 h old) | yes, 14:04:17 (first tick 14:03:46 skipped: leg cap $0 on a wide book) | all seven seen |
| entry filter | cost share 0.068 → kept | all skipped, every tick to 16:08: cost share > 1/3 (RLC, SAND, ORCA, SKL, API3), book too thin (UMA, CHR) |
| EV floor / slots / one-carry-per-symbol | passed (no slot row for a shadow-only strategy = no per-strategy cap; carry floor 30 bps) | — |
| wallet risk gate + book sizing | $500 ceiling, impact cap $3 012, risk gate 2 % of equity → **$196.82** | — |
| booked | `shadow` wallet, `context.is_shadow = true`, `book_open` stamped | — |
| funding | 15:00 −0.50 %, 16:00 −0.19 % booked from the ticker stream (+$1.37, 69 bps) | — |
| borrow | quote 0.75 bps/h × 3 per started hour ($0.13 after 3 h) | — |
| close | at horizon 10-11 14:04 | — |

Live borrow quotes at signal time are the binding cost, as the adversarial check predicted:
RLC/ORCA/SKL/API3 quoted 0.66–0.88 bps/h (×3 over 48 h = 95–126 bps), against 250–388 bps of naive
expected funding. KAIA's own rate fell from −0.50 % to −0.19 % at its second settlement: the decay
below, live.

**Fix**: the paper engine closed any carry after five minutes of adverse predicted funding
(`funding_flip`). The study held 48 h, and a flip exit made it worse there (+82 vs +104 bps train);
here each early exit also pays four walked legs on a fraction of the funding. Book-priced carries
(context names a spot leg) now hold to horizon (e8bdd2f). ~~Not changed, noted: the equity mark of an
open carry is `rate × hours/8`, wrong by the interval factor for 1/2/4 h coins.~~ Fixed 2026-10-09 in
69f37c8: an open carry is marked at the funding it has settled (accumulated per position) minus the full
round trip and the borrow accrued so far.

**Funding-decay model.** Realised 48 h funding (entry 1 h after settlement, as in the replay) divided
by the naive expectation, median per cell of prior run × depth, fitted on train (2 941 book-sized
episodes, cells with n < 30 back off to the run bucket). Run = consecutive settlements ≤ −0.05 %
immediately before the signal one (Bybit funding history). Turnover and interval improved the train
fit by < 0.04 in median |log error| and were left out.

| run \ depth (bps/interval) | 8–15 | 15–30 | 30–60 | ≥ 60 |
|---|---|---|---|---|
| 0 (fresh spike, 74 % of episodes) | 0.21 | 0.13 | 0.10 | 0.11 |
| 1–2 | 0.30 | 0.25 | 0.13 | 0.27* |
| 3+ | 0.62 | 0.50 | 0.35 | 0.52* |

\* back-off. Persistent squeezes keep paying; fresh deep spikes unwind. The rule (model above,
keep iff stressed borrow + book ≤ decayed expectation) was chosen on train, then evaluated once on the
holdout. Borrow ×3, t by ISO week; $ = net × book-sized leg:

| filter | split | kept | net bps (t) | median | $ total | top 5 % share | top 5 coins | coins | ×10 borrow |
|---|---|---|---|---|---|---|---|---|---|
| naive, 1/3 (live) | train | 1 081 | +175.6 (6.7) | +12.7 | 6 620 | 0.52 | 0.28 | 217 | +30.9 (1.5) |
| decay, ×1.0 | train | 576 | +237.7 (6.9) | +66.9 | 5 090 | 0.39 | 0.30 | 140 | +119.5 (3.8) |
| naive, 1/3 (live) | holdout | 221 | +292.5 (6.3) | +96.8 | 2 261 | 0.41 | 0.46 | 89 | +122.7 (2.6) |
| decay, ×1.0 | holdout | 91 | **+463.6 (4.9)** | +238.0 | 1 390 | **0.33** | 0.62 | 38 | **+313.3 (3.3)** |
| kept by naive only | holdout | 139 | +165.4 (4.2) | +36.0 | 885 | 0.47 | 0.46 | 74 | −15.3 (−0.4) |

Median realised / expected: naive 0.21 train, 0.30 holdout; decayed 1.00 train, 1.36 holdout
(funding was more persistent in the holdout, so the model is conservative there).

**Not adopted as the gate.** Net per kept episode rises and episode concentration falls, as
required, but coin concentration rises (top 5 coins 62 % vs 46 %, 38 vs 89 coins) and the holdout
money falls 39 % at ×3 borrow. The episodes only the naive rule keeps earn +165 bps at ×3 borrow and
−15 at ×10: whether they pay depends on the borrow the shadow book has yet to measure. So the
module records `expected_decayed_bps`, `decay_ratio`, `prior_run`, `naive_keep` and `decay_keep` on
every signal (a085008), and `MATRIX_NFC_EXPECTED_MODEL=decay` switches the gate. Revisit when ≥ 30
closed shadow episodes exist: if the naive-only ones lose net of actual borrow, switch. Scripts:
session scratchpad `decay/` (`features.py`, `fit.py`, `ho.py`, `PRE_HOLDOUT.txt`).

**Opportunity rate vs the tracker's "2 in 72 h".** Bybit funding history for all 363 USDT perps
whose coin is borrowable on Bybit or Binance (fetched 16:00 UTC): settlements ≤ −0.08 % numbered
**125 in the last 72 h (26 episodes, one per coin per 48 h)** and 1 248 in 30 days (218 episodes,
~51 a week; weekly 30–64). The replay's ~50/week holds; the market is not quiet. The tracker saw 2
because watchlist coins stream only since 13:14 today. Replaying the current watchlist rules
(hourly at :40, cap 20, enter −0.05 %, exit −0.02 %, 6 h keep, incumbent ×1.5, prior-day turnover)
over 30 days: with the predicted rate proxied by the next settled one, the watchlist would have held
211 of 218 episodes (24 of 26 in 72 h); on settled rates alone 74 of 218. The predicted-rate trigger
does the work and the selection rules need no change. 5 of the 218 were in the traded universe.

## Borrow measurement (2026-10-09)
The shadow book measured funding and book costs for real but borrow only by assumption (entry
quote × 3). Three changes make borrow measured, at least as quoted:

**Recorder.** `ingestion.borrow_recorder` (in `ingestion-market`) polls the two public tables the
strategy reads, Bybit spot-margin VIP0 (260 coins with a rate) and Binance cross-margin VIP0 (481),
every 10 min into `margin_borrow_rates` (local DB, migration 0041; ops: `docs/wiki/operations.md`).
Neither venue publishes pool size or utilisation; the only extra field is the per-account limit
(`max_borrow`, coin units). Quoted ≠ lendable still holds: a dry pool is invisible without a signed
query.

**Paper accounting.** A carry's close now charges each started hour at the rate recorded for that
hour on its borrow venue (in force at the hour start if ≤ 75 min old, else the first quote inside
the hour), ×1; the kill switch's open-carry mark uses the same helper (`carry_books.carry_borrow`).
Hours the series misses fall back to entry quote × `MATRIX_CARRY_BORROW_STRESS` (3).
`context.borrow_source` = `series` | `stressed_entry` | `mixed`, plus `borrow_hours_series`,
`borrow_hours_fallback`, `borrow_series_mean_hourly`. KAIA (opened 14:04, recorder from 16:16) will
close `mixed`; carries opened from now on close `series` unless the recorder stops.

**Quoted borrow vs funding depth**, snapshot 2026-10-09 16:12 UTC, 363 Bybit USDT perps whose coin
has a rate on either venue; borrow = cheapest venue (as the strategy picks), funding = Bybit
predicted rate normalised to 8 h:

| funding bps/8h | n | borrow bps/8h p25 / median / p75 / p90 | Bybit median | Binance median |
|---|---|---|---|---|
| ≤ −20 | 6 | 5.3 / 5.8 / 6.6 / 7.0 | 11.2 | 5.8 |
| −20 … −8 | 5 | 6.5 / 6.5 / 6.6 / 18.3 | 6.6 | 6.5 |
| −8 … −5 | 7 | 0.8 / 1.8 / 3.3 / 7.3 | 1.4 | 3.0 |
| −5 … −2 | 20 | 0.9 / 2.1 / 5.5 / 18.3 | 1.0 | 3.5 |
| −2 … 0 | 32 | 0.3 / 0.6 / 2.5 / 4.7 | 0.6 | 2.2 |
| 0 … +0.5 | 15 | 0.2 / 0.4 / 0.7 / 1.1 | 0.5 | 0.5 |
| +0.5 … +1 (default rate) | 271 | 0.7 / 2.5 / 4.8 / 6.6 | 0.9 | 4.2 |

The adversarial check's "6.2 at ≤ −8 vs 0.4–1.0 elsewhere" reproduces near zero funding, but the
bulk of coins at the default +1 bps/8h (mostly Binance-only alts) borrow at a median 2.5: the
squeeze premium is ~2.5× over the population and ~10× over the cheap near-zero group. Over the 70
negative-funding coins, **ln b8 = 0.082 + 0.341 ln|f8|** (bps/8h; r 0.41; slope 95 % bootstrap CI
0.14–0.52): borrow rises with the cube root of depth. Outliers matter: LUNC at −21 bps/8h borrows at
0.08, KAIA costs 12.0 on Bybit vs 6.0 on Binance.

**What the stress should be.** The entry filter reads the quote at signal time, on a coin that is
already in a squeeze, so that quote already carries the depth premium. The ×3 was a replay device
(historic squeezes priced at today's calm quote); live it double-counts. What remains is how borrow
moves during the 48 h hold. Mapping Bybit funding of the last 30 days (363 coins, 289 episodes
settled ≤ −0.08 %, one per coin per 48 h) through the fit, hold-mean borrow / entry-level borrow:

| entry depth bps/interval | n | mean | median | p75 | p90 | share > 1 |
|---|---|---|---|---|---|---|
| 8–15 | 131 | 0.68 | 0.63 | 0.81 | 0.97 | 9 % |
| 15–30 | 76 | 0.67 | 0.62 | 0.84 | 0.97 | 8 % |
| 30–60 | 55 | 0.61 | 0.58 | 0.83 | 0.94 | 5 % |
| ≥ 60 | 27 | 0.54 | 0.51 | 0.70 | 0.79 | 0 % |
| all | 289 | 0.65 | 0.61 | 0.81 | 0.96 | 7 % |

At the upper slope (0.52) p90 is 0.96 too (max 2.0). Funding decays, so depth-tracking borrow falls.

**New default** (`neg_funding_carry`, `MATRIX_NFC_BORROW_MODEL=depth`): borrow = max(quote, depth
floor) × 48 h × `MATRIX_NFC_BORROW_HOLD_STRESS` (1.0, the p90 above rounded up), floor = the fit's
median quote at the signal's depth. The floor binds when a quote lags or is unusually cheap (LUNC,
SAND; KAIA's 1 h interval makes its 8 h depth 400 bps, floor 1.05 bps/h vs quote 0.75). The 1/3
rule is unchanged. `flat` restores quote × 3; every signal also records `borrow_flat_bps`,
`borrow_depth_bps`, `borrow_depth_floor_bps` and `flat_keep` (the old verdict). On today's deep
coins the borrow share of naive expected funding falls from 0.22–1.14 (flat) to 0.07–0.38: RLC
0.22 → 0.07, SKL 0.62 → 0.21, MINA 0.78 → 0.26, F 1.14 → 0.38. The filter now keeps more episodes,
so the book cost and the 1/3 rule do most of the gating.

Why this is not loosening blind: the shadow book now charges what was quoted hour by hour, so
episodes kept only by the depth model (`flat_keep = false`) are judged on measured quoted borrow.
The evidence is weak (one snapshot, r 0.41, cross-section standing in for within-coin dynamics).
**Revisit** when ≥ 30 shadow episodes close with `borrow_source = series`: (1) hold-mean series /
entry quote, p90 > 1.0 → set `HOLD_STRESS` to it; (2) the `flat_keep = false` episodes net of series
borrow: mean ≤ 0 → back to `flat`; (3) with ≥ 2 weeks of recorder data, refit borrow on depth
within coin. Scripts: session scratchpad `borrow/` (`snap.py`, `fetch_fh.py`, `hold.py`), not
committed.

**Shadow tracker:** ~~the decomposition reads `borrow_charged_usd` and does not know its source.~~
Done 2026-10-09 in 845bd64: the report splits by `borrow_source` and by the `flat_keep` / `decay_keep`
arms, evaluates the three revisit rules over `series` episodes only, and sends `review_due` once at 30
([[operations]] "Shadow tracker"). For a by-hand check:
`SELECT context->>'borrow_source', count(*) FROM predictions WHERE strategy_id =
'neg_funding_carry' AND status <> 'open' GROUP BY 1` (shared) tells series from fallback; an episode
charged at `stressed_entry` or `mixed` tells nothing (or only part) about real borrow.

## Round 4: dated-futures basis — 2026-10-09: 27 of 66 cells survive, all coin-margined; flat all of 2026
Question: every funding carry failed because its yield floats. Long spot + short a **dated** future held to delivery locks
the yield at entry: the future converges to the index. Does it beat a stablecoin lending rate out of sample, net of
costs, on total capital? Scripts, pre-registration (committed alone in 17edd54 before any data) and its log:
`services/backtest/research/signal_2026_10_r4/`. The 66 cells were registered in `docs/research/ledger.jsonl` (98bcd2d)
before any return was computed; q comes from the ledger (**m = 174** at finalisation).

**Design.**
- Data: Binance's public archive (data.binance.vision) for every dated contract: 52 USD-M and 222 COIN-M, 1h klines
  plus mark klines, 2020-08 to now. Deribit 1h candles for 54 BTC/ETH quarterlies, from 2020; expired instruments are
  still served. Binance spot 1h, plus spot 1m on expiry days. Published delivery prices. Today's books on Binance,
  Deribit, Bybit and OKX. Bybit's v5 API and OKX do not serve candles for delivered contracts, so those two venues
  appear only in today's capacity and basis check.
- Structures:
  - **LIN**: Binance spot + short Binance USD-M quarterly on isolated margin N/L, L ∈ {1, 2, 3}. Capital is N + N/L.
  - **INV**: Binance spot, used as collateral, + short coin-margined quarterly on Binance COIN-M or Deribit. The
    contract count C = N·F0/S0 locks the USD value at N·F0/S0. Capital is N.
- Underlyings: BTC, ETH, and ALT. ALT means the COIN-M alts BNB, SOL and XRP. ADA, BCH, DOT, LINK and LTC have no live
  quarterly today, so there is no book to price them: excluded and counted.
- Rule: a daily decision at 08:00 UTC on the best quarterly with 14 ≤ DTE ≤ 200. Net annualised basis
  ((F/S − 1) − 4 taker fees − walks at $5k) × 365/DTE must be ≥ Y, Y ∈ {5, 8, 12} %/yr. Then open one tranche at the
  09:00 closes.
- Exits:
  - HOLD to delivery: spot is sold as a TWAP over the venue's averaging window against the published delivery price.
  - EARLY when the remaining gross basis is ≤ 2 %/yr.
- Fees: spot 10 bps, futures 5 bps, and delivery charged as a taker leg. No funding and no borrow.
- Metric per tranche: annualised return on total capital minus the **OKX USDT Simple Earn lending rate** over the same
  hold. The rate is a public hourly series, 2021-12 onward. Before that, its first-month mean (1.4 %) is used, which
  flatters 2021 train.
- Split by entry: train 2021-01 to 2024-06, holdout 2024-07 to 2026-10-09. Pass: mean > 0 with week-clustered t ≥ 2,
  then the holdout gate plus ledger BHY q ≤ 0.05.

**Data fixes before any cell statistic** (logged in PREREG):
- Binance's delivery-price API stamps the date at 00:00, and it keeps only about the last 12–18 deliveries.
- Its averaging window changed from 60 to 30 min. For each expiry, the window whose spot TWAP matches the published
  price is used.
- Where no price is published (2021 to mid-2022), settlement is at the 30-min spot TWAP. Where prices exist, the
  measured tracking is 1 ± 6 bps. The futures' last 1h close is off by ± 80 bps.

**Results** ($5k per leg, HOLD; EARLY is within 0.4 %/yr on BTC/ETH). e = excess annualised return on capital, in %/yr
over USDT lending. t_ctr = t clustered by contract. mw = money-weighted excess.

| cell | train e (t_wk) | n | holdout e (t_wk) | t_ctr (contracts) | n | mw | gross ann vs rf | q | verdict |
|---|---|---|---|---|---|---|---|---|---|
| INVD_BTC Y5 | +7.7 (8.1) | 670 | **+2.9 (8.2)** | 3.7 (6) | 471 | +2.8 | 8.3 vs 5.5 | 4e-9 | survives |
| INVD_BTC Y8 | +9.4 (7.6) | 483 | **+4.6 (7.5)** | 3.9 (4) | 178 | +4.5 | 11.2 vs 6.6 | 1e-6 | survives |
| INVD_BTC Y12 | +15.3 (9.4) | 256 | +6.8 (6.2) | 5.5 (3) | 51 | +7.0 | 14.2 vs 7.4 | 0.002 | survives |
| INVB_BTC Y5 | +8.6 (8.6) | 703 | **+3.0 (6.8)** | 2.9 (6) | 450 | +3.0 | 8.6 vs 5.5 | 3e-7 | survives |
| INVB_BTC Y8 | +10.9 (8.4) | 498 | **+5.6 (8.2)** | 4.1 (4) | 177 | +5.5 | 11.8 vs 6.1 | 3e-7 | survives |
| INVB_BTC Y12 | +16.8 (10.6) | 280 | +8.8 (16.9) | 17.0 (3) | 77 | +8.9 | 14.1 vs 5.3 | 4e-8 | survives |
| INVB_ETH Y5 / Y8 / Y12 | +10.1 / +12.8 / +17.4 | 613 / 473 / 292 | +2.8 (5.6) / +5.6 (8.1) / +8.0 (5.7) | 3.3 / 6.0 / 6.1 | 368 / 145 / 41 | +2.6 / +5.2 / +8.5 | | ≤ 0.024 | survive |
| INVD_ETH Y5 / Y8 | +8.6 / +11.1 | 601 / 439 | +2.4 (5.3) / +5.0 (7.2) | 2.9 / 4.2 | 342 / 131 | +2.3 / +4.9 | | ≤ 1e-4 | survive |
| INVD_ETH Y12 | +15.9 (10.0) | 265 | +7.5 (4.9) | 10.4 (3) | 35 | +8.0 | | 0.075 | rejected (q) |
| INVB_ALT Y5 / Y8 / Y12 | +10.0 / +12.4 / +17.4 | 745 / 580 / 344 | +2.7 (4.1) / +5.0 (4.9) / +8.3 (5.3) | 3.9 / 5.7 / 14.7 | 514 / 227 / 79 | +2.4 / +4.8 / +8.5 | | ≤ 0.007 | survive (Y12 EARLY q 0.087) |
| LIN_BTC 1× Y5 / Y8 / Y12 | +1.5 (1.7) / +2.0 (1.8) / +5.8 (4.3) | 553 / 422 / 245 | −1.6 / −0.7 / +0.7 (0.9) | | 447 / 164 / 57 | −1.5 / −0.6 / +1.4 | 4.0–6.9 vs 5.6–6.3 | 1 | rejected |
| LIN_BTC 2× Y12 | +7.1 (3.4) | 245 | +3.0 (3.5) | 2.2 (3) | 57 | +3.5 | | 0.087 | rejected (q) |
| LIN_ETH 1× Y8 / Y12 | +3.2 (2.9) / +5.4 (3.5) | 385 / 250 | −0.6 / −0.4 | | 140 / 39 | −0.5 / +1.0 | | 1 | rejected |
| LIN 2×/3× Y5–Y8 | −1.1 … −16.5 | | −4 … −14 | | | | | 1 | rejected (train) |

### Claims
- **The locked basis beats USDT lending, but only by +2.5 … +9 %/yr, and only when it is wide.**
  - In the holdout the coin-margined trade grossed 8–15 %/yr on capital, against 5.3–7.6 % for lending over the same
    windows.
  - Excess return is positive in every half-year: 2024H2 +1.9 … +2.6, 2025H1 +2.4 … +4.3, 2025H2 +2.4 … +3.8 (Y5).
  - **The rule has not been in the market on a single day of 2026.** Median best-quarterly gross basis per year
    (BTC Deribit): 2021 11.2 %, 2022 1.9, 2023 4.5, 2024 11.2, 2025 6.9, **2026 2.8**.
  - Days in market at Y5 (BTC): 2021 90 %, 2022 13 %, 2023 32–41 %, 2024 100 %, 2025 73–79 %, 2026 0 %. This is a
    regime trade on leverage demand. Lending rates move with it (OKX USDT: 12.8 % in 2023H2–24H1, 1.5–2.3 % now).
- **Today (books 16:59 UTC) it is flat.**
  - BTC quarterlies: 4.4–4.8 %/yr gross at 77 DTE and 4.9–5.3 % at 168 DTE, on Binance, Deribit, Bybit and OKX alike.
    Venues agree within about 0.5 %/yr.
  - ETH: 3.2–4.5 %. SOL and XRP COIN-M: 2.0–3.1 %.
  - Net of four fees, BTC is about 3.3 / 4.4 %/yr, below Y = 5. USDT lending today is 2.9 %.
- **Coin-margined is the only structure that pays; USDT-margined at any leverage does not.**
  - LIN at 1× ties up N + N on two balances, which halves the yield. Holdout excess was −1.6 … +0.8 %/yr.
  - At 2× or 3× without top-ups, **26–42 % of Y5 tranches are liquidated** (BTC 2× 26 %, 3× 32 %; ETH 33 % / 42 %).
    These are bull-market tranches: the short leg's mark rose a median 15 % (BTC) / 26 % (ETH) within the hold, p90
    76 % / 87 %, max +111 % / +166 %.
  - Never being liquidated would need a top-up of p90 29 % / 40 % of notional at 2×, and up to 65 % / 126 %.
  - INV cannot be liquidated by construction. Equity in coin is C/P_t, the value of the position itself, so equity /
    maintenance = 1/mm at every price, even when the short marked +175 % (BTC) or +1 569 % (an alt).
  - A unified or portfolio-margin account that counts spot BTC as collateral for a USDT future would get INV's economics
    on linear contracts. Untested here: the haircut and its liquidation rules are venue-specific.
- **Capacity is not the constraint on BTC.**
  - Per leg, walk costs are $500 0.0 / $5k 0.0–0.6 / $50k 1.2–5.1 bps on Binance and Deribit BTC quarterlies, and
    0.0 / 0.0 / ≤ 0.6 on BTC spot.
  - Holdout excess at $500 / $5k / $50k: INVD_BTC Y5 +2.87 / +2.86 / +2.83; INVB_BTC Y8 +5.64 / +5.62 / +5.56.
  - ETH on Deribit thins at $50k (Y5 +2.43 → +1.39); Binance COIN-M ETH holds (+2.75 → +2.63).
  - Alts lose about 20 % at $50k: SOL and XRP quarterlies walk 15–30 bps at $5k.
  - Bybit and OKX linear quarterlies are thin beyond the front month: OKX USD-margined next quarter walks 121 bps even at
    $500.
- **The t-statistics overstate the evidence.** Tranches on one contract share their regime and their settlement. With
  contract clustering, t is 2.9–6 for the Y5/Y8 survivors, but over only 4–7 contracts each. The week-clustered gate is
  what was pre-registered, and that gate passed. The honest effective sample is a handful of quarterly regimes. EARLY
  annualises short holds: train ALT EARLY shows +40 %/yr against a money-weighted +15. Read mw, not the mean, for
  EARLY.
- Settlement is clean. Spot TWAP against the published delivery price: 1 ± 6 bps (Binance), 1 ± 7 bps (Deribit).

**Verdict: survives the pre-registered bar (27 cells, all coin-margined). It is a real but modest, regime-bound yield:
about +3 %/yr over USDT lending at Y5 and +5 %/yr at Y8 when in market, nearly zero tail risk in the payoff, the risk
being custody. It is idle now and has been all of 2026.** Best cells for routing: **INV BTC, Y8, HOLD** (Binance COIN-M or
Deribit): highest holdout excess with ≥ 4 contracts and flat capacity to $50k/leg. Y5 trades 3–5× more days for about
half the excess. EARLY adds nothing on BTC/ETH.

**Counterparty / operational (qualitative).**
- Capital sits on one exchange for 2–6 months per tranche, in coin.
- Deribit needs the spot coin withdrawn from where it was bought (a withdrawal fee and latency, not modelled).
- The spot leg is bought with USDT and the future settles on a USD index, so USDT/USD drift is unhedged and not
  modelled.
- Binance COIN-M's delivery fee is charged as a 5 bps taker leg, which is conservative. Deribit's current futures taker
  is 3.5 bps (5 charged).

### Strategy module design (for routing; no code written)
- `dated_basis_carry`, crypto, shadow first. Universe: BTC (then ETH) quarterlies on one coin-margined venue. Data comes
  from public endpoints: Binance `dapi` ticker/depth and `/futures/data/delivery-price`, Deribit `get_order_book` /
  `get_delivery_prices`.
- Entry: decide daily at 08:00 UTC. Best quarterly with 14 ≤ DTE ≤ 200. b_net = ((F_mid/S_mid − 1) − 4 fees − both
  walks at the intended size − the TWAP exit walk) × 365/DTE ≥ Y (8 %), and b_net − current USDT lending ≥ 0. Execute
  after a full bar.
- One tranche per day: spot buy N, short C = N·F0/S0 USD of contracts.
- Exit: hold to delivery. In the last 30 min before 08:00 UTC, sell spot by TWAP (Binance's window is 30 min since
  2024; read it off the published price each time). The future settles at D. No roll logic: new tranches go to whichever
  quarterly the rule picks, and delivered collateral returns to cash.
- Sizing is capital-bound, not risk-bound. N per tranche = a fixed slice. Cap total locked capital at a share of equity,
  because it is illiquid for months. The per-trade risk gate still bounds N. No leverage beyond coin-margined 1×. The
  payoff has no stop-loss path; the kill switch should flatten both legs together, never the short alone.
- **What the paper engine lacks:**
  1. A dated-future instrument: expiry, contract size, an inverse payoff in coin, and a delivery-price source.
  2. A two-leg position held to expiry whose short leg has **no funding**. The carry book today assumes spot + perp
     with funding; it needs a "dated" leg kind and a **settlement event** at expiry that books the future at D and
     the spot at its TWAP.
  3. A mark for equity and the kill switch: value = (N/S0)·S_t·F0/F_t, marked at both mids. This follows the 2026-10-09
     rule: a mark changes in the same commit as the close.
  4. Collateral accounting: the spot leg *is* the margin. The wallet must not double-count it as free cash, and it must
     lock N until delivery.
  5. Ingestion of quarterly tickers and books (only Bybit perps, a Binance funding poller and spot stream today).
  6. An evidence path: carries reach `status` only via closed realised episodes (net_of_costs). A tranche's natural
     null is the lending rate, not zero, and its clustering unit is the contract. The bar's registered n (≥ 200) means
     ~1 year in market at one tranche a day.
- Expected contribution, read honestly:
  - At Y8 in a 2024-like year: about +5 %/yr over lending on the capital committed, i.e. ~$500/yr per $10k, nearly
    riskless in price.
  - In 2026 so far: $0, because the rule never fires.
  - It is worth building only as a cheap, mostly idle sleeve that switches on when basis widens. It is not a fix for
    today's PnL.

Data stays in the session scratchpad `r4/data/` (Binance archive pickles, Deribit candles, delivery prices, OKX lending,
books, `tranches_{500,5000,50000}.pkl`).

## Round 5: options-implied — 2026-10-09: 14 cells, nothing survives
Question: do extremes in Deribit option prices (volatility risk premium, 25-delta skew, DVOL spikes, IV
term inversion, put/call demand) predict the BTC / ETH perp over 3–7 days by enough to pay a taker round
trip and funding? Slow signals only; the target was ≥ 45 bps gross per episode. Scripts, pre-registration
and its timestamped log: `services/backtest/research/signal_2026_10_r5/` (`PREREG.txt` committed alone in
1b2b6e1 before any data was fetched). Cells registered in `docs/research/ledger.jsonl` (27aaf25) before any
return; train verdicts committed in 79bb6cf; record-only holdouts and finals after that.

**Design.**
- Data (public): Deribit DVOL 1h for BTC and ETH from 2021-03-24. Every Deribit option trade in the
  20:00–24:00 UTC window of each day from `history.deribit.com`: 4 050 windows, 4.83 M trades, each with its
  `iv`. Bybit BTCUSDT / ETHUSDT 1h klines and funding. Today's Bybit books. Deribit's own historical-vol
  endpoint serves only ~16 days, so realised vol comes from the perp klines.
- Features at 00:00 UTC, from data before then:
  - VRP = DVOL − 30-day realised vol;
  - RR25 = median iv of 0.15–0.35-delta calls minus the same for puts, 20–45 DTE (Black-76 delta from the
    trade's iv);
  - TERM = ATM iv at 1.5–10 DTE minus ATM iv at 45–120 DTE;
  - PC3 = 3-day put/call notional;
  - DVOLchg = 24 h log change of DVOL.

  Each feature is ranked against its own trailing 365 days (≥ 120 values). RR25 is missing on 36–49 % of
  days, because the 4 h window is thin in the 25-delta bucket. TERM is missing on 19–30 %.
- 14 cells, direction fixed in advance from the economic prior:
  - A.hi: VRP ≥ p90, long. A.lo: VRP ≤ p10, short.
  - B.put: RR25 ≤ p10 (puts rich), long. B.call: RR25 ≥ p90 (calls rich), short.
  - C: DVOLchg ≥ p95, fade the last 24 h move.
  - D: TERM ≥ p90 (front-end inversion), long.
  - P: PC3 ≥ p90, long.

  Each is held 3 d or 7 d. BTC and ETH are pooled, with one episode per signal window and no overlap per
  asset. Entry is the close of the bar ending 01:00 UTC.
- Cost: 2 × 5.5 bps taker, plus the walked spread at $5k (BTC 0.012 bps, ETH 0.040 bps round trip:
  negligible), plus every funding settlement over the hold on mark-to-market notional. Total ≈ 11 bps plus
  funding.
- Split: train 2021-03-24..2024-06-30, holdout 2024-07-01..2026-10-08. Pass: train net > 0 with
  week-clustered t ≥ 2. Then holdout net > 0, t ≥ 2, ledger BHY q ≤ 0.05 and net ≥ +15.

**Result: no cell passes train** (k = 0), so no holdout opened. The holdouts below are record-only rows in
the ledger and never enter q. Net is bps per episode; t is clustered by entry ISO week.

| cell | train net (t_wk) | n | train gross | holdout net (t_wk) | n | holdout median | verdict |
|---|---|---|---|---|---|---|---|
| A.hi.3 VRP high → long 3d | +185.4 (1.72) | 39 | +196.4 | −17.3 (−0.13) | 29 | −83.0 | rejected (train) |
| A.hi.7 | −34.2 (−0.24) | 33 | −23.1 | +87.5 (0.37) | 23 | −75.2 | rejected |
| A.lo.3 VRP low → short 3d | −26.7 (−0.36) | 36 | −15.7 | +50.8 (0.56) | 27 | +29.1 | rejected |
| A.lo.7 | +8.5 (0.05) | 30 | +19.5 | −3.5 (−0.02) | 21 | +97.7 | rejected |
| B.put.3 puts rich → long 3d | −10.6 (−0.08) | 50 | +0.4 | −19.0 (−0.37) | 80 | +7.7 | rejected |
| B.put.7 | −14.6 (−0.05) | 34 | −3.6 | −24.2 (−0.21) | 57 | +75.1 | rejected |
| B.call.3 calls rich → short 3d | −107.6 (−1.57) | 71 | −96.6 | −124.9 (−1.23) | 23 | −112.8 | rejected |
| B.call.7 | −65.4 (−0.54) | 52 | −54.4 | −278.3 (−1.67) | 18 | −333.6 | rejected |
| C.3 DVOL spike → fade 3d | −132.7 (−1.72) | 85 | −121.7 | −73.3 (−0.85) | 59 | +21.7 | rejected |
| C.7 | −166.2 (−1.50) | 74 | −155.2 | −231.7 (−1.72) | 47 | −110.5 | rejected |
| **D.3 IV inversion → long 3d** | **+135.7 (0.77)** | 48 | +146.7 | **+115.1 (1.61)** | 70 | +200.8 | rejected (train t) |
| D.7 | +13.5 (0.05) | 38 | +24.6 | +82.1 (0.81) | 53 | +205.1 | rejected |
| P.3 put/call high → long 3d | +22.1 (0.30) | 90 | +33.1 | −46.5 (−1.18) | 114 | −23.2 | rejected |
| P.7 | +122.8 (1.18) | 75 | +133.8 | +1.9 (0.02) | 89 | −17.7 | rejected |

**Cumulative m = 171** (ledger, 2026-10-09): 91 from rounds 1–3b, 14 from round 5 and 66 from round 4.
Every round-5 cell has q = 1. H1 is still the only test with q ≤ 0.05, at q = 9.4e-5.

### Claims
- **Options extremes do not time BTC/ETH over 3–7 days.** Episodes are rare (8–50 a year pooled) and the
  per-episode spread is several hundred bps, so a +100…+200 bps mean is still t < 2. A clean pass would have
  needed t ≈ 3.9 against BHY at m = 171, which is out of reach at these sample sizes.
- **Cost was never the problem.** On BTC/ETH perps the round trip is about 11 bps and funding −17…+21 bps
  per episode. Every cell's gross moves by ±100–300 bps, and the sign of the gross decides each cell, not the
  cost (cost ×2 shifts the nets by −11).
- **The contrarian priors are wrong in sign on skew and DVOL spikes.** Calls-rich (B.call) and fade-the-spike
  (C) lose in both splits, and so did the price leg: these extremes behave as **momentum**, not reversal.
  The mirror rules (follow the skew, follow the spike move) were not pre-registered. Their sign-flipped
  figures, +54…+267 gross with t ≤ 1.72, are post-hoc and need a new registration on new data before they
  count for anything.
- **Benchmark drift flatters every long cell.** The unconditional 00:00-entry long earns, in price bps:
  BTC +30 / +68 train and +19 / +48 holdout at 3 d / 7 d; ETH +32 / +72 and +11 / +29. A long cell must
  beat that, not just zero. D.3's +115 holdout exceeds it by about +100 bps, but at t 1.6.
- **Closest to an edge: front-end IV inversion → long 3 d (D.3).** It is positive in both splits (+136 /
  +115, medians +166 / +201, 64–65 % winners) but fails the train t (0.77). In train, the best five weeks
  (+10 705 bps) and the worst five (−10 888) cancel. By year: 2022 −28 (n 29), 2023 +241 (6), 2024 H1 +453
  (13); holdout 2024 H2 +208 (21), 2025 +65 (35), 2026 +100 (14). Inversion in the 2022 bear market was not a
  bottom. It is the only family where train and holdout agree in sign and
  size. **Reopening it** takes a new pre-registered forward test: a live daily feature (Deribit public chain,
  no history needed), long BTC/ETH 72 h when TERM ≥ trailing p90, with n fixed in advance (≥ 60 episodes,
  which is about 2 years at its ~30-a-year holdout rate). Re-cutting this history would only be more
  searching.
- **Data limits.**
  - The 4 h window is a sample of the day: skew and term are measured on 20:00–24:00 UTC trades only.
    Median-of-trades iv is noisier than a fitted surface.
  - Black-76 delta uses the index, not the forward. That shifts the bucket edges by a few delta points at
    45–120 DTE.
  - No survivorship issue (BTC/ETH).
  - Data stays in the session scratchpad `r5/data/`: DVOL, klines, funding, books, 4 050 trade windows
    (357 MB JSON) and `features.pkl` / `episodes.pkl`.

**Verdict: rejected.** No module is designed. What follows for the main session is nothing to build. Options
data does not belong in the live feature set until a forward test of D.3 is pre-registered and passes.

### Forward test r5f (pre-registered 2026-10-09)
D.3 is now being settled on data that did not exist when it was registered: `r5f.D.3`, n fixed at **60 episodes**,
pre-registered through the harness's forward mode (`services/backtest/research/signal_2026_10_r5f/PREREG.md`, committed
alone in d283c9a; register row in the ledger, **m = 172**).
- Rule: exactly round 5's D.3 (`matrix_shared.iv_term`, which reproduces round 5's TERM, percentile and all 118 D.3
  entries bit for bit): TERM from the Deribit option trades in [D−4h, D), pct against the asset's trailing 365 days
  (≥ 120 values), fire at ≥ 0.90, one episode per signal window, entry at the close of the bar ending D + 1h, 72 h hold,
  no TP/SL, BTC and ETH pooled. Forward window: decision days from 2026-10-10.
- Success: net > 0 with week-clustered t ≥ 2 over the first 60 episodes, **and** BHY q ≤ 0.05 at the ledger's cumulative m
  at evaluation time. Evaluated once, by `forward.py decide`, when `forward.py status` says all 60 have closed (about 2–3
  years at ~21 a year; never decided if not reached by 2031-01-01). Nothing is evaluated earlier.
- Power is low and is written into the pre-registration: at round 5's per-episode SD (718 bps) a true +125 bps gives
  E[t] ≈ 1.35 at n = 60; +200 bps gives ≈ 2.2. A pass is strong evidence; a fail does not exclude a smaller edge.
- Live pieces: `ingestion.iv_term_recorder` writes `deribit_iv_daily` (migration 0043) each day after 00:00 UTC and
  backfilled the last 400 days ([[operations]]); the shadow module `iv_inversion` (strategy_configs crypto v1,
  status `shadow`) emits the long on the shadow wallet from 01:00 UTC on a window's first day. Its band in
  `params.shadow_band` (+166…+201 bps from round 5's medians, floor 0, `min_episodes` 60) keeps the shadow tracker at
  `collecting` until 60; the tracker reports, the harness decides.
- Verified live 2026-10-09 18:02 UTC: the backfill wrote 802 rows (2025-10-04..2026-10-09) and its 800 days that overlap
  round 5's cache equal round 5's TERM exactly. Decision 2026-10-09: BTC TERM −3.11 at pct 0.341, ETH −9.47 at 0.119 →
  no signal. `forward.py status`: 0 of 60 closed. Tracker: `shadow iv_inversion/crypto: COLLECTING — 0/60`.

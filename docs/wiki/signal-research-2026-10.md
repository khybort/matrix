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
endpoints, 1 h cache), and stamps `borrow_rate_hourly`; the paper engine's
carry close charges it over the hold (working-tree hunk inside the uncommitted
carry WIP in `paper_trade.py`; ships with that WIP).

## Open questions
- **Borrow availability** is the binding unknown: a listed rate is not a
  lendable pool, and squeezed coins are exactly the ones whose pools run dry.
  Settles only with a signed account query at signal time (Phase 5).
- **Coverage**: the edge is outside the 25-symbol universe. Decisive next step
  is ingestion streaming ticker + trades for every borrowable perp whose
  settled funding ≤ −0.08 % (screener already polls all tickers). Without it
  the shadow book gathers ~5 episodes a month.
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

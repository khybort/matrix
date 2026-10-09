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

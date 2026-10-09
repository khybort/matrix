---
title: Market and cadence study — should BIST/US run, and does 15 s pay?
updated: 2026-10-09
sources: ["signal replay 2026-10-09 (matrix_shared.edge_study: _load_candidates, one_per_episode, entry_index, simulate_bracket, welch; gap guard: entry bar <= 3 min old, horizon span <= 1.5x + 5 min; 10 random-time controls per signal)", "db: paper_positions ⋈ wallets ⋈ predictions, closed since 2026-09-01", "~/.claude/matrix_usage/*.jsonl (LLM ledger, 2026-09-13…10-09)", "docker compose logs ingestion-market/agent (retained since 2026-09-19)", "db: market_bars_{bist,us} created_at vs ts, pg_stat_user_tables, raw_documents by source", "docker stats 2026-10-09 12:30 UTC"]
status: current
---

Two questions from [[open-questions]], measured on the same clean signal set
as [[strategy-scoreboard]]: non-shadow directional predictions since
2026-09-01, **one per episode**, entry at the last *closed* 1m bar, net of the
market's round trip (`2 × execution_cost_bps`: crypto ~15, BIST 40, US 8).

**Entry-rule caveat (2026-10-09, cda6ee6).** The "strict" model here is the
pre-signal rule the edge study withdrew. The "centred" model is close to the
honest one. Per-strategy levels shift by up to ±12 bps under the honest rule
([[edge-study]] "Correction — pre-signal path"). Both verdicts stand: BIST is
40 bps of cost against single-digit gross, and the cadence result holds under
both price models. **Both were applied 2026-10-09 in 9091f38.**

## Should BIST and US be running? — No. Pause both (2026-10-09)

| | crypto | BIST | US |
|---|---|---|---|
| closed paper since 09-01 | 6 429 trades, −$665.17, **−22.8 bps** | 105, −$47.21, **−60.1 bps** | 28, −$1.24, **−23.8 bps** |
| last fill | 09-29 | 09-21 | 09-24 |
| signals: raw → episodes simulated | 26 356 → 8 828 | 1 989 → 638 | 349 → 309 |
| pooled net per episode (t) | −17.5 (−18.8) | **−43.0 (−10.4)** | **−11.4 (−5.6)** |
| best strategy, net | funding_reversion / matrix_agent −11 | bist_gap_fade −26.2 | matrix_agent −11.4 |
| best vs random entry (t) | matrix_agent +3.1 (1.49) | bist_gap_fade +14.0 (1.75) | matrix_agent −3.7 (−1.7) |
| round trip | ~15 bps | 40 bps | 8 bps |
| data latency | real-time ws | **~15 min** (yfinance: bar 12:17 written 12:32:37) | not measured live |
| share of agent LLM calls, weekday | ~62 % | ~18 % (~250/day, ~$3.6 imputed) | ~20 % (~285/day, ~$4.1 imputed) |
| other load | — | 1 811 of 2 827 news docs since 09-01 are Turkish (dunya, bloomberg_ht, hurriyet) → graph extraction | 918 448 1m bars re-inserted on the 10-09 restart (7-day yfinance backfill, 497 symbols); `market_bars_us` 1.25 GB, the largest bar table |
| log noise (ingestion-market, retained log) | bybit ws/funding warnings | 12 909 yfinance "no price data" lines | 28 259 yfinance lines; 68 % of the whole log is BIST+US yfinance |

Per-strategy, net bps per episode (strict entry, 15 s): bist_gap_fade −26.2
(n=133), bist_intraday_reversion −32.4 (67), bist_news_event −38.1 (17),
bist_volume_breakout −53.7 (210), matrix_agent/bist −46.8 (211),
matrix_agent/us −11.4 (309). **No BIST or US strategy beats random entry at
t≥2**, and BIST's 40 bps round trip is 3× any gross level measured there.

**Verdict: pause BIST and US.** *Applied 2026-10-09 ~12:40 UTC: the 6 BIST/US `strategy_configs` rows set to `paused`, ingestion `--markets crypto`, agent `--interval 60` (see CHANGES.md).* Neither has a strategy whose *gross* level
clears its cost, so the only thing they can do with capital is lose it; the
regime-diversification argument does not apply to strategies with no edge
(diversifying zero-edge books diversifies costs). BIST additionally decides on
15-minute-old prices, so its signals cannot be executed at the price they
were computed on — paper results there are optimistic by construction. What
they do cost: ~38 % of the agent's weekday LLM calls when the LLM is up, most
of graph extraction's input, the largest bar table, and two thirds of the
ingestion log. Default wallets already give every BIST strategy 0 slots;
signals keep flowing (≈130–300 BIST predictions a day through 10-06).

**How to pause** (reversible; nothing is deleted):
1. Signals — both the agent (`agent.config.load_agent_config`) and the
   strategy service (`strategy.main._active_configs`) only run rows with
   `status IN ('active','shadow')`; any other status makes them skip the
   market before features or LLM:
   ```sql
   -- shared DB; 5 BIST rows + 1 US row today
   UPDATE strategy_configs SET status = 'paused', updated_at = now()
   WHERE asset_class IN ('bist','us') AND status IN ('active','shadow');
   ```
   Resume: the same statement with `'paused'` → `'active'`.
2. Data — `docker-compose.dev.yml` ingestion-market command
   `--markets crypto bist us` → `--markets crypto` (base `docker-compose.yml`
   passes no `--markets`, i.e. all registered: add `"--markets", "crypto"`),
   then `docker compose up -d --no-deps ingestion-market`.
3. Optional — the Turkish RSS feeds are hard-coded in
   `ingestion/news.py:DEFAULT_FEEDS`; no switch exists. Only matters once the
   LLM backend is healthy again (graph extraction is the largest LLM consumer).

**Re-entry criterion:** a BIST/US hypothesis whose replayed gross level
clears the round trip at the promotion bar ([[methods]]), on real-time data.
For BIST that first needs a non-delayed feed.

## Is the 15-second cadence justified? — No

The same episodes, entered after a decision delay *d*, same horizon, TP/SL and
cost. Two price models, because 1m bars cannot resolve 15 s directly:
**strict** = last closed bar at t+d (never looks ahead, but its price is on
average 30 s *before* t+d); **centred** = the bar whose span contains t+d−30 s
(its close is at t+d on average). Differences are paired per signal.

Net bps per episode, centred, and the change from 15 s:

| strategy | mkt | n | 15 s | 60 s | 300 s | Δ60 (t) | Δ300 (t) |
|---|---|---|---|---|---|---|---|
| grid | crypto | 3 367 | −15.4 | −14.0 | −13.7 | +1.4 (2.12) | +1.7 (1.38) |
| matrix_agent | crypto | 1 900 | −10.5 | −12.3 | −10.3 | −1.9 (−1.32) | +0.2 (0.07) |
| dca | crypto | 1 370 | −15.0 | −13.5 | −11.8 | +1.6 (1.34) | +3.2 (1.74) |
| funding_reversion | crypto | 1 221 | −13.2 | −8.5 | −12.7 | +4.7 (2.54) | +0.5 (0.19) |
| oi_delta | crypto | 375 | −26.6 | −22.7 | −18.3 | +4.0 (0.86) | +8.4 (0.95) |
| momentum_xs | crypto | 323 | −33.2 | −35.5 | −30.7 | −2.3 (−0.68) | +2.4 (0.28) |
| oi_breakout | crypto | 181 | −36.1 | −22.3 | −35.6 | +13.8 (1.43) | +0.5 (0.04) |
| **crypto pooled** | | 8 737 | **−15.5** | **−14.1** | **−13.8** | **+1.4 (2.37)** | +1.7 (1.73) |
| crypto pooled, strict | | 8 828 | −17.5 | −14.9 | −14.6 | +2.5 (4.25) | +2.9 (2.85) |
| bist pooled | | 525 | −40.9 | −44.5 | −46.0 | −3.6 (−2.87) | −5.1 (−1.73) |
| bist pooled, strict | | 638 | −43.0 | −41.3 | −43.7 | +1.8 (1.27) | −0.7 (−0.27) |
| matrix_agent | us | 283 | −8.8 | −8.8 | −7.5 | 0.0 (−0.09) | +1.2 (1.01) |

- **Acting faster does not earn more.** In crypto, waiting 60 s instead of
  15 s is *better* by 1.4–2.5 bps (t=2.4–4.3, both price models): after a
  signal the price on average moves against its direction (grid and
  funding_reversion are fading moves; the entry improves with patience). No
  strategy is net positive at any cadence; cadence is not where the money is
  lost.
- **The momentum names look speed-sensitive only under the strict model, and
  that is an artefact.** Strict entry shows oi_breakout losing 15.6 bps and
  oi_delta 6.7 bps between "0 s" and 15 s (t≈2.4). The "0 s" bar closes on
  average 30 s *before* the signal and contains the move that triggered it, so
  it is a price nobody could trade. Centred, the same names improve with delay.
- **BIST is indeterminate and moot**: the two price models disagree in sign,
  and the data the strategies read is already ~900 s old.
- **The 15 s loop was never 15 s on the LLM path.** On 4 weekdays with a
  working backend (09-24/25/28/29) the agent made 1 382 calls/day, about one
  per tick (≤12 symbols per chunk), p50 latency 18.6 s, p90 33 s; the median
  gap between decisions was **39 s** (p10 31, p90 74) because the 15 s sleep
  starts after the LLM returns. Imputed cost $20.7/weekday (subscription, not
  billed). Graph extraction used ~5 900 calls/day ($40.7) against the same 4
  global slots — the decision loop's rate budget is consumed by graph, not by
  its own cadence.
- **Since 2026-09-30 every LLM call fails** (ledger: 0 successes 10-01…10-09;
  agent log: 309 subscription timeouts, circuit breaker open; cursor fallback
  "Authentication required"). The agent has been rule-only for 9 days; the
  rate-budget question is currently academic. *Update, later on 10-09:* the
  backend answered again after the VM egress came back, which made the
  [[llm-value-audit]] contention windows possible. The agent was then made
  `rule_only` by choice (6a0884e).

**Recommendation (applied 2026-10-09, 9091f38):** decision cadence 60 s for the agent
(`docker-compose.yml` `agent` command `--interval 15` → `60`, same in
`docker-compose.dev.yml`; `DEFAULT_INTERVAL_S` in `agent/main.py`). Measured
cost: none (crypto +1.4 bps, t=2.4); 4× fewer rule ticks, and roughly half the
agent's LLM calls once the backend is back, which leaves slots for whatever
earns. Keep the paper engine's 5 s exit monitor: that is risk management, not
decision cadence. 300 s is not better than 60 s by a margin worth the horizon
risk on 60-minute strategies.

## Caveats
- Crypto bars and trades are missing 10-01…10-08 (ingestion was down while
  strategies kept emitting); crypto evidence is 09-05…09-30.
- `entry_price_ref` is not a usable signal-time price for dca and BIST
  strategies (mean gap to the bar at t: +38 and up to +314 bps), so drift is
  measured bar-to-bar, never against the stored reference. For US it agrees
  with the bars within 0.2 bps.
- LLM share per market is inferred from the weekday hourly profile (crypto-only
  hours ~37 calls/h, BIST hours ~69, US hours ~81); the ledger has no market
  field. Graph's BIST share is by document count, an upper bound.

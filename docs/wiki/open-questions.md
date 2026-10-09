---
title: Open questions
updated: 2026-10-09
status: current
---

What is genuinely unknown, phrased so a future session can close it. Ranked by
how much the answer would change.

## Claims
- ~~Does any signal in the book have edge before costs?~~ **Re-answered
  2026-10-09: no measured one.** The 2026-09-20 answer (`momentum_xs` +36 bps,
  t=6.04, n=799) was pseudo-replication: 87 % of those rows were one three-hour
  v1 burst on 2026-09-13 re-emitting ten (symbol, side) bets every ~90 s.
  Counted as episodes the strategy is −10.7 bps vs random entry (t=−0.98,
  n=285); on 2026-09-21 −22 bps. The 155 fills of 2026-09-21 lost −30.7 bps,
  and the same simulator on those signals says −26.2 gross, so the wallet
  captured the signal within ~5 bps — there was no edge to lose
  ([[edge-study]] Correction). The study now counts episodes, not rows. Live
  question: **does any strategy beat random entry when each bet counts once?**
- **Is the horizon wrong, or the entry?** *Answered for momentum_xs
  2026-10-09 — the entry* (n=155: MAE median 149 vs MFE median 115 bps; MFE
  reached TP in 28, MAE reached SL in 41; [[edge-study]]). 57 % of exits are horizon exits at
  roughly minus cost — though part of that was fills arriving with only half
  the horizon left, which is now fixed. *Test*: for closed trades, measure max favourable and
  adverse excursion within the horizon. If MFE frequently exceeds the TP
  distance, the problem is exit timing; if not, the entry has no predictive
  content.
- ~~**Can the EV ranker discriminate at all?**~~ **Answered 2026-10-09: no.**
  All 8 891 crypto signals of 30 days (one per episode, gap-guarded replay,
  [[strategy-scoreboard]]) bucketed by the engine's `_ev`: every decile is
  net negative, the top decile (−21.0 bps) is no better than the bottom
  (−18.3), and Spearman(EV, net) is **−0.057 (t=−5.4)** — slightly inverted.
  Same on the bare `confidence x tp − (1−confidence) x sl` (−0.052), on BIST
  (−0.002, n=644) and US (+0.084, t=1.5, n=312). Across strategies the score
  mostly sorts by *which module* emitted (grid fills D1 and D10 alike) and each
  module's confidence is on its own scale (matrix_agent ~0.13, bist_* ~0.95).
  Within strategy the best is momentum_xs (+0.118, t=2.2) — not enough to
  build on. Consequence: the ranker cannot be what selects a paying trade, and
  the floor it feeds is only correct because nothing in the book pays.
  *Next*: rank on a measured, per-strategy calibrated forward return instead
  of self-reported confidence, and test it the same way.
- **Do the carry strategies have edge?** The books said 0 % wins
  (inverse_carry −27 bps n=53, xexch −29 bps n=20). That was accounting:
  every carry closed on Bybit's post-settlement `+0.0000125` placeholder and
  accrued at it ([[paper-engine]]; fixed 2026-10-09). Replayed with
  settlement accounting, one per episode: inverse_carry **+14.9 bps net,
  t=0.95, n=25** (unproven; spot-borrow cost not modelled), xexch −25.6
  (t=−3.56), cash_and_carry −31.1 (t=−6.26) — those two have no edge under
  either model. *Test*: let the fixed accounting run; re-measure
  inverse_carry at n≥60 episodes with a borrow-cost assumption.
- **What is the true cost model?** Slippage is now measured per symbol from our
  own book snapshots ([[methods]]), but fees remain assumed and nothing is
  validated against real fills. Until live or exchange-simulated fills exist,
  every net-of-cost conclusion still carries that assumption.
- **Is the 15-second cadence justified?** Nothing has shown that faster
  decisions earn more than they cost in spread. *Test*: compare realised PnL of
  the same strategy at 15 s vs 60 s vs 300 s decision intervals.
- **Should BIST and US be running at all right now?** They add surface area,
  cost and attention while crypto has no edge. The argument for keeping them is
  regime diversification; there is no evidence yet either way.
- ~~**Someone else holds this bot token.**~~ — closed 2026-09-20 by rotation.
  The finding stands and is recorded in [[incidents]]: with every local consumer
  stopped, a lone traced poller took 3 conflicts in 240 s at an in-flight count
  of 1, host-side long polls returned 409 on 2 of 8 and later 1 of 4, and no
  container or process here held the token. The operator revoked it in
  BotFather — proof the revoke landed is that the old token began answering
  `getMe` with `Unauthorized` — and `scripts/rotate_telegram_token.sh` installed
  the replacement. *The lesson that outlives the incident:* a bot token pasted
  into a chat window is a leaked credential, and the failure it causes is
  silent, because sending keeps working while receiving is stolen.

- **Uncommitted, live, and mine to land later:** the EV floor lets a
  *measured* edge outrank the model's own `confidence x tp_pct` estimate. The
  floor was rejecting 174 of 178 candidates an hour (2026-09-20), every
  momentum_xs signal among them, while that strategy measures +21 bps at the
  95% lower bound against a ~12 bps round trip. The change is written, tested
  (38/38) and running in the dev tree, but the EV floor itself is another
  author's work that has not reached git, so there is no HEAD to commit it
  against. Land it the moment their floor lands — until then it exists only in
  the working tree and would be lost by a hard reset.
- **`shared_buffers` is 128 MB against a 49 GB table.** That is the Postgres
  default and nobody has revisited it. Measured 2026-09-20: a 60.4% buffer
  cache hit rate across 880M block reads, which means most of the trading
  path's reads go to disk. Raising it to a few GB is very likely the single
  largest infrastructure lever left, and it costs a database restart — brief,
  non-destructive, and everything reconnects, but disruptive enough that it
  should be a deliberate act rather than a side effect of a working session.
  *Test:* set it, watch `cache_hit_pct` and the feature-path query times.
- **`autovacuum_work_mem` is 64 MB, which forces repeated index passes.** It
  holds about 11M dead tuple ids at 6 bytes each; `market_trades` carried 26.5M
  dead on 2026-09-20, so one vacuum has to scan all four indexes three times
  over — and one of those indexes is now 3.5 GiB. Raising it to a few hundred
  MB would make it a single pass. Unlike `shared_buffers` this needs only
  `ALTER SYSTEM` plus `pg_reload_conf()`, no restart, but it is still a global
  change and the current pass would not pick it up. *Test:* set it, then
  compare `index_vacuum_count` at the end of the next pass.

  The other half of the same picture is the throttle: the worker's wait event
  sits at `Timeout/VacuumDelay`, i.e. it is sleeping on
  `autovacuum_vacuum_cost_delay = 2 ms` against `vacuum_cost_limit = 200`.
  Raising the limit for this table would multiply its throughput. It was
  deliberately **not** done on 2026-09-20: an unthrottled maintenance job on
  this table is exactly what degraded the feature path earlier the same day
  ([[incidents]]), and the arithmetic says no rescue is needed — ~120 GB free
  against 8.7 GiB/day of lag-driven growth is 14 days, and once the pass lands
  the deletions free roughly sixteen times what ingestion consumes, so the
  file should stop extending on its own. Measured while it ran: the worker
  reads ~6.7 MB/s of index under the throttle and `num_dead_tuples` sat at
  11,184,524, exactly the 64 MB work_mem capacity — which is the three-pass
  prediction confirmed from the other side. The indexes measure **54 GiB**, not
  the 30 first guessed here: `ix_market_trades_exchange_id` alone is 29 GiB,
  `market_trades_pkey` 13, `ix_market_trades_symbol_ts` 8.3, and the new
  `ix_market_trades_ts` 3.5. That puts a pass near 2.3 hours and the whole
  vacuum near 7, during which the file gains about 1.5 GiB against ~120 GiB
  free. Revisit only if a pass fails to complete or the runway drops under a
  week.

  Worth noticing separately: 54 GiB of index against a table meant to hold
  seven days of data, and more than half of it is a unique index that exists
  only to deduplicate inserts. Both shrink in proportion as the backlog
  drains — check again once it has.
- **A time-only index changes plans elsewhere.** `ix_market_trades_ts` (0039)
  made `WHERE symbol = $1 ORDER BY trade_ts DESC LIMIT n` switch from the
  composite index to a backward scan of the time index with a symbol filter.
  For a liquid symbol that is fine; for a rare one it walks most of the index
  and ran over nine minutes. `ANALYZE` fixed the rare case, and
  `autovacuum_analyze_scale_factor = 0.02` (0040) keeps the statistics fresh
  so it stays fixed. Worth re-checking after the backlog drains, because the
  distribution will shift again.
- **Operator-blocked**: only `TELEGRAM_BOT_TOKEN` remains — without it every
  alert is invisible, which is what let the three-day outage pass unnoticed.
  The wallet refund was applied 2026-09-19 and lid sleep was disabled
  2026-09-20, so the system can finally accumulate an uninterrupted sample.

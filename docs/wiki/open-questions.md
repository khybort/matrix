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
- ~~**Is the 15-second cadence justified?**~~ **Answered 2026-10-09: no.**
  The same 8 737 crypto episodes entered 60 s after the signal instead of
  15 s net +1.4 bps (t=2.4; strict-bar model +2.5, t=4.3); 300 s +1.7. No
  strategy is net positive at any cadence, and on the LLM path the loop was
  really ~39 s (median gap; p50 call 18.6 s + 15 s sleep). Recommended: agent
  `--interval 60`. Since 09-30 every LLM call has failed, so the agent is
  rule-only ([[market-cadence-study]]).
- ~~**Should BIST and US be running at all right now?**~~ **Answered
  2026-10-09: no — pause both.** Since 09-01, one per episode: BIST −43.0 bps
  net (t=−10.4, n=638; round trip 40 bps, data ~15 min delayed), US −11.4
  (t=−5.6, n=309); no strategy in either beats random entry at t≥2. Paper:
  BIST −60 bps on 105 fills, US −24 on 28. They took ~38 % of weekday agent
  LLM calls and two thirds of the ingestion log. Switch: `strategy_configs`
  status → `'paused'` for `asset_class IN ('bist','us')` plus ingestion
  `--markets crypto` ([[market-cadence-study]]). Re-entry needs a hypothesis
  whose gross clears the round trip, on real-time data.
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
- **`shared_buffers` is 128 MB against 54 GiB of index.** Still the Postgres
  default (re-checked 2026-10-09). Measured 2026-09-20: a 60.4% buffer cache
  hit rate across 880M block reads; 2026-10-09 read 88.3%, but over a few
  minutes since the crash-recovery restart reset the counters, so not
  comparable. The heap is now only 5.2 GB, which makes this cheaper to fix than
  it was: a few GB would hold the hot heap and the upper levels of every index.
  It costs a database restart — brief and non-destructive, but a deliberate
  operator act, not a session side effect. Do the REINDEX below first; it
  shrinks what the cache has to hold by ~50 GiB. *Test:* set it, watch the hit
  rate over a day and the feature-path query times.
- **~~Will autovacuum keep pace / how long is the disk runway?~~ Answered
  2026-10-09:** the backlog drained. `market_trades` is 18.3M rows in a
  **5.2 GB heap** (from ~49 GB; vacuum truncated the emptied tail), oldest row
  2026-09-30, the database 84.5 GiB (from 116 GiB on 09-20), the VM volume
  190 GiB free of 591, the host 198 GiB free. Runway is not a concern. At this
  size one vacuum pass fits in the 64 MB `maintenance_work_mem` (11.18M dead
  tuple ids; ~8M dead at the time of measuring), so the three-pass problem is
  gone without touching `autovacuum_work_mem`, and the throttle question with
  it. (`last_autovacuum` reads NULL only because the 10-06 power loss reset the
  statistics.)

  What did **not** resolve: the four indexes are still **54 GiB**
  (exchange_id 29, pkey 13, symbol_ts 8.3, ts 3.5). The earlier note here
  expected them to shrink as the backlog drained; B-trees do not — emptied
  pages are reused, never returned. So every vacuum pass walks 54 GiB of index
  for a 5 GB table (a pass sat in "vacuuming indexes" for 9+ min on 10-09), and
  the feature path's index reads compete for 128 MB of cache. *Fix:*
  `REINDEX INDEX CONCURRENTLY` on each, smallest first (`ix_market_trades_ts`
  3.5 GiB → a few hundred MB expected); no write lock, cancels the running
  autovacuum, which is fine. Not done: the session's permission policy refused
  it as a shared-resource change, so it waits for the operator.
- **A time-only index changes plans elsewhere.** `ix_market_trades_ts` (0039)
  made `WHERE symbol = $1 ORDER BY trade_ts DESC LIMIT n` switch from the
  composite index to a backward scan of the time index with a symbol filter.
  For a liquid symbol that is fine; for a rare one it walks most of the index
  and ran over nine minutes. `ANALYZE` fixed the rare case, and
  `autovacuum_analyze_scale_factor = 0.02` (0040) keeps the statistics fresh
  so it stays fixed. Worth re-checking after the backlog drains, because the
  distribution will shift again.
- **Operator-blocked** (2026-10-09): Telegram is configured and verified;
  what remains is physical. (1) **Keep the laptop on AC.** It ran flat on 10-06
  and cost 2.9 days; at 11:59 on 10-09 it was unplugged again. The host
  watchdog now alerts while on battery, but nothing local can speak once the
  host is off. (2) After a power loss the Mac waits at the login window and nothing —
  LaunchAgents, OrbStack, containers — starts until someone logs in (10-09:
  3 hours). FileVault is off, so enabling automatic login (System Settings →
  Users & Groups) closes this; a physical-security trade only the operator
  can make.
  (3) The `REINDEX CONCURRENTLY` and `shared_buffers` items above.

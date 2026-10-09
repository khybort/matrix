---
title: Open questions
updated: 2026-10-09
status: current
---

What is genuinely unknown, phrased so a future session can close it. Ranked by
how much the answer would change.

## Open — trading

- **Is borrow actually available on a squeezed coin, at the quoted rate, for
  the size we want?** This is the binding unknown behind `neg_funding_carry`.
  A listed rate is not a lendable pool, and squeezed coins are the ones whose
  pools run dry. The 36–40× gap between funding and quoted borrow on the
  top-5 % episodes is itself evidence that borrow was not available at that
  rate ([[signal-research-2026-10]] "Adversarial check"). Public data cannot
  settle it. The recorder (`margin_borrow_rates`) measures *quoted* borrow
  only, and neither venue publishes pool size. *Settles only with* a signed
  account query at signal time (max loanable + rate), which is Phase 5 and
  needs mainnet keys, a human decision ([[risk-gates]]).
- **Does `neg_funding_carry`'s shadow mean clear +30 bps per episode?** The
  pre-registered band is +100…+300 with a floor of +30. If the edge is real,
  expect a median far below the mean, a third to half of episodes losing a
  little, and PnL arriving in a few squeezes. A mean at or below +30 means
  quoted borrow and book cost, not funding, decide it, and it gets no capital.
  *Test, already running:* the shadow tracker's verdict at 20 closed episodes
  (48 h holds; 0 closed / 3 open at 2026-10-09 16:55 UTC). After that comes
  `review_due` at 30 `series`-borrow episodes, with the three revisit rules
  (hold stress, flat vs depth borrow model, naive vs decay gate). Capital
  needs `confirmed` from `carry_edge_rows`: ≥ 20 day clusters, the
  pre-registered n with a floor of 200, then BHY and deflated Sharpe
  ([[operations]] "Shadow tracker", [[edge-study]] "Carry evidence"). At
  ~50 opportunities a week before the filter, `confirmed` is months away,
  not weeks.
- **"No edge anywhere": what is left to test?** Every strategy in the book is
  `unproven` or worse. 91 pre-registered research cells produced one fragile
  carry. The strategic question is whether the system keeps searching price,
  funding and flow data that rounds 1–3b exhausted, or turns to families
  never tested here. Untested: dated-futures basis (round 4, in progress),
  forward re-tests of the H8b/H11 near misses, a real liquidation feed,
  options-implied signals, on-chain flow, listing events, cross-asset
  conditioning, and maker quoting as a strategy. Ranked list with the
  reasoning: [[research-backlog]] "What has not been tested". Until something
  passes, the profitable action is to trade less. The EV floor already does
  that.
- **What is the true cost model?** Slippage is measured per symbol from our
  own book snapshots ([[methods]]). Carries walk real books at open and close.
  Fees are still assumed, and nothing is validated against real fills. Until
  live or exchange-simulated fills exist, every net-of-cost conclusion
  carries that assumption.
- **Is there edge in a *subset* of the book's signals that a different filter
  would find?** The EV ranker is not that filter (below). A per-strategy,
  calibrated forward-return ranker has not been built or tested.
  Meta-labelling failed (AUC 0.52 on episodes; [[methods]]).

## Answered 2026-10-09

- ~~Does any signal in the book have edge before costs?~~ **No.**
  momentum_xs's +36 bps was pseudo-replication (5475933). On episodes with the
  honest entry it is −32.1 vs random (t=−2.92), and nothing beats a null
  ([[edge-study]]).
- ~~Is the horizon wrong, or the entry?~~ **The entry**, measured on
  momentum_xs: MAE median 149 vs MFE median 115 bps over 155 trades
  ([[edge-study]] "MFE / MAE"). Book-wide, no strategy's entry beats random,
  so horizon tuning has nothing to harvest.
- ~~Can the EV ranker discriminate?~~ **No.** Spearman(EV, net) −0.057
  (t=−5.4, n=8 891), with every decile net negative. The score mostly sorts by
  emitting module ([[strategy-scoreboard]]).
- ~~Is the 15-second cadence justified?~~ **No.** Acting at 60 s is +1.4 bps
  (t=2.4), and the agent runs `--interval 60` since 9091f38
  ([[market-cadence-study]]).
- ~~Should BIST and US be running?~~ **No.** BIST −43.0 bps net (t=−10.4),
  US −11.4 (t=−5.6), neither beats random entry. They are paused (9091f38);
  the switch is in [[market-cadence-study]].
- ~~Does the LLM or the graph earn its rate budget?~~ **No.** LLM side minus
  rule lean −2.4 bps (t=−0.40) in the same hours, and graph coverage changed
  no decision's return. The agent is `rule_only` and graph heuristic (6a0884e;
  [[llm-value-audit]]).
- ~~Does maker execution turn any strategy positive?~~ **No.** It is worth
  +4…+6 bps an episode, but every arm sits 12–30 bps under zero
  ([[maker-execution]]).
- ~~Do the carry strategies have edge (0 % wins)?~~ **That was accounting:**
  Bybit's post-settlement placeholder (94e55cf). Carries now book funding per
  settlement and charge recorded borrow and walked books ([[paper-engine]]).
  On evidence: cash_and_carry and xexch_funding_arb are retired (round 3 /
  3b). inverse_carry measures +11.9 bps over 22 episodes, unproven. The
  hedged version that survives research is `neg_funding_carry` (open, above).
- ~~The EV floor's measured-edge override is uncommitted.~~ Landed in
  5164dbc. Its motivating case (momentum_xs +21 bps at the lower bound) was
  withdrawn the same day.

## Open — infrastructure

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
  (3) The `REINDEX CONCURRENTLY` and `shared_buffers` items above, plus
  `autovacuum_work_mem` via `ALTER SYSTEM`. At today's 5 GB heap that one
  is headroom, not urgent.

## Closed — operations

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

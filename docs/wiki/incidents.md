---
title: Incidents
updated: 2026-10-09
sources: [docs/CHANGES.md, git log, "~/.orbstack/log/vmgr.log", "pmset -g log", "log show (powerd)", "docker logs", predictions/outcomes tables]
status: current
---

What has actually gone wrong, with the root cause and the fix. Most entries are
*silent* failures: the system looked healthy and produced nothing. That is the
dominant failure mode here, and the reason liveness is measured by output age
([[operations]]).

## Claims
- **2026-06-10 → 09-12, ~3 months down.** Nobody was running it. Recovered
  2026-09-12; everything before that date is historical.
- **2026-09-13 — challenger positions in the champion wallet.**
  `_open_for_market(shadow=True)` resolved the wallet a second time without the
  flag, so every challenger position was booked into the default wallet with no
  per-strategy slot cap: `funding_reversion` v3 did 1 198 trades and −$343 in
  one day. Fixed in 74eb6ff; the −346.76 refund was applied 2026-09-19
  (`make reset-capital`, [[pnl-reality]]). All champion/challenger comparisons before this date are
  invalid.
- **2026-09-13 — a test truncated live `dev_tasks`.** The dev_agent conftest
  pointed at the live database; 9 task rows were lost and the live worker
  picked up a test task. Restored from the 15:35 backup; tests now use an
  isolated database.
- **2026-09-13 → 09-14, 11 hours down.** MacBook entered *Clamshell Sleep*; the
  OrbStack VM slept with it and the Docker socket hung. A kernel OOM in the
  10 GiB VM was a contributing (not root) cause. Fixes: per-container memory
  caps, VM raised to 12 GiB. The lid itself kept costing time (another 21 h on
  2026-09-19/20) until `sudo pmset -a disablesleep 1` was applied 2026-09-20.
- **2026-09-14 — the Claude credentials sync had never run.** launchd cannot
  execute a script under `~/Documents` (TCC, exit 126); the job looked
  installed. Moved to `~/.matrix/bin`, interval 30 min.
- **2026-09-14 — two daemons did not survive the host sleep.** The Bybit
  WebSocket stayed "open" and silent for 5 hours (`ping_interval=None`, no
  `ConnectionClosed`), and the bars tick hung. Fixes: reconnect after 60 s
  without a frame, bound each tick at 240 s.
- **2026-09-16 08:16 → 09-19 00:00, three days of no trading.** Postgres
  briefly reported "the database system is in recovery mode"; the paper
  engine's child process died on it. `watchfiles.run_process` only restarts on
  file changes, so the container stayed "Up" with nothing inside — 8 positions
  frozen, no opens, no closes, and no alert, because notify's detectors were
  also not reaching Telegram (token unset). Fixed 2026-09-19 by making the dev
  entrypoint a real supervisor (095b2c4).
- **Smaller silent bugs, same family**: `momentum_xs` stamped every version as
  v1 so reflection saw n=0 for the live ones; the bars aggregator walked a
  284 M-row index every minute; Director filed tasks into the wrong database
  tier; phantom slot rows diluted every real strategy's share to 3 slots
  instead of 8.

## 2026-09-13 → 10-09 — Four measurement artefacts that read as edge

None of these lost money directly, because nothing was sized on them for long.
But each one made a zero-edge signal look tradeable, and the learning loop
acted on two of them. Withdrawn on 2026-10-09, in this order:

1. **Re-emissions counted as trades** (5475933, 6dcc8f1, 8a28470).
   momentum_xs's +36 bps (t=6.04) came from one three-hour v1 burst that
   re-emitted ten bets every ~90 s. It reached `confirmed`, got 1 → 7 slots
   and Kelly sizing at the 2 % gate, and the 90-minute horizon proposal was
   built on it. On episodes it is −10.7 (t=−0.98).
2. **Scoring across bar gaps** (2a72463). During the outage the "entry" was
   the last bar before the hole. bist_volume_breakout read +116 bps on 244
   take-profits, and matrix_agent/crypto read `confirmed` at +44.8. Both
   registry entries are voided.
3. **The pre-signal minute inside the entry bar** (cda6ee6). Up to 60 s
   before the signal was scored. That flattered breakouts (oi_delta −11.9
   bps once removed) and charged reversion entries; grid's "worse than
   chance" came from this.
4. **Bybit's post-settlement funding placeholder** (94e55cf). Every
   inverse/xexch carry "flipped" at its first settlement and booked about 0
   funding. Their 0 % win rate was the four-leg fee.

**Lesson:** one definition of a bet, called by every consumer
(`edge_study.episode_groups`); every arm of a study enters after the signal;
never score across a hole. Details are in [[edge-study]] and
`docs/ENGINEERING_LESSONS.md`.

## 2026-09-20 — The bot token was never ours alone

**Symptom:** `notify` logged `Conflict: terminated by other getUpdates request`
every 40–140 s with a full traceback, from the day the token was installed.

**What it looked like:** a second copy of our own bot — a stale container, a
leftover debug script, a restart overlap. All three were checked and none held.
Widening the long poll from 10 s to 30 s cut request churn threefold and
changed nothing.

**How it was settled:** instrument, don't theorise. A traced poller run alone
in the notify image logged every `get_updates` call's start and end: its own
in-flight count never exceeded 1, yet it took 3 conflicts in 240 s. Host-side
`curl` long polls, with every container stopped, returned 409 on 2 of 8. An
inventory of every running container's environment found no other holder. Three
independent vantage points, one conclusion: the competitor is outside this
machine.

**Why it mattered more than the noise:** Telegram delivers each update to
exactly one `getUpdates` caller. A competing consumer takes the operator's
commands, and because *sending* is unaffected, alerts kept arriving and nothing
looked wrong. This is the same failure shape as the three-day outage — the
channel that reports health was itself impaired, silently.

**Fix:** conflicts are counted and collapsed to one line, an urgent alert goes
out hourly over the path that still works, and `scripts/rotate_telegram_token.sh`
makes the swap a single command. The operator revoked the token the same day;
the old one now answers `getMe` with `Unauthorized`, which is how a revoke is
verified. The replacement was installed by the script and the channel confirmed
working. Watch the conflict counter rather than the traceback: if it climbs
again on a fresh token, the leak is somewhere that keeps getting the new one.

## 2026-09-20 — Retention ran for eight days and deleted almost nothing

**Symptom:** none. That is the point. `bars-aggregator` logged
`retention: pruned market_trades=200` every few minutes, which reads exactly
like a drained steady state.

**Reality:** `market_trades` held 322M rows going back to 2026-06-01 against a
seven-day policy, the local database was 112 GB, and the postgres volume was
78% full. Growth measured at 1.56 GB/day *net of* the pruner.

**Cause:** a query plan, not the code. The pruner's inner
`SELECT ctid ... WHERE ts < cutoff AND symbol = $1 LIMIT 10000` gave Postgres a
LIMIT over a predicate it estimated at tens of millions of rows, so it chose a
sequential scan. For a symbol with nothing old left the LIMIT never fills, the
scan reads all 322M rows, and that one query eats the whole 45-second budget —
every run, before reaching the symbols that had data to delete. `ANALYZE` had
never run on the table either, so the estimates the planner used came from
nothing.

**Fix:** `ORDER BY` the policy's timestamp column, which makes the existing
`(symbol, ts)` index strictly better than a scan. Measured: the empty-symbol
case falls from minutes to **0.9 ms**, and a full batch returns 10,000 rows in
800 ms. Two tests guard it — one against the real planner, one against the
shipped SQL string.

**The lesson worth keeping:** a maintenance job that reports success is not
evidence that it is working. This one had a log line, a budget, a test suite
and a Makefile target, and it was still a no-op for eight days. Check the
quantity it is supposed to move — here, the age of the oldest surviving row —
not whether it ran. Same shape as [[open-questions]]' contested bot token:
the signal that would have told us was itself broken.

**Resolved 2026-09-20, in two parts.** The plan bug first, then the structural
one: per-symbol deletion is simply wrong for this table. `market_trades` is append-only and ~900
symbols interleave, so the oldest rows of any single symbol are scattered
across the whole 97 GB heap. Deleting 10 000 of them touches 10 000 distinct
cold pages: measured at ~1 s warm but **three to six minutes cold**, and the
budget is only checked between batches, so one run does roughly one batch.
That was ~1.8M rows/day against ~4.3M/day arriving, so the pruner still lost.

The fix is to sweep in **time order across all symbols** rather than per
symbol: the same 10 000 rows then come from ~180 contiguous pages instead of
10 000 scattered ones. Three batches of 20 000 measured 1.1 to 2.1 seconds
each. That needed an index on `(trade_ts)` alone (migration 0039, built
CONCURRENTLY over ~80 minutes, 3.5 GB), because the ordered sweep is *worse*
than what it replaces without one — with the index still building, the planner
answered the same query with a Gather Merge sort that ran 24 minutes across
6.18M buffers. With the index valid both regimes are index scans: cost 844 for
20 000 rows while the backlog lasts, cost 4.59 once the predicate matches
nothing.

Time partitioning remains the cleaner long-run shape, and a one-time rewrite
keeping seven days would return ~90 GB at once rather than to the free-space
map. Both are operator decisions and neither is needed now.

Deleted space also returns to Postgres' free-space map, not to the OS. New
inserts reuse it, so at best the disk trend flattens rather than falls;
`make db-compact TABLE=market_trades` (VACUUM FULL) is the operator-run,
table-locking way to give it back.

**Side note worth its own fix one day:** every restart of `bars-aggregator`
leaves its Postgres backend still executing the DELETE it was in the middle
of — visible as two concurrent prune statements from the same client address,
with a shutdown traceback in the connection pool. Editing anything under
`packages/python-shared/src` restarts that container, so a working session
quietly stacks orphaned backends against the table it is trying to drain.

## 2026-09-20 — Measured models that never reached the services reading them

**Symptom:** `momentum_xs` dropped from 7 slots to 3 within seconds of a
container reload, right after the same pass had promoted it on a confirmed
+29.7 bps edge.

**First cause, and mine:** the edge study cache lived only in the process, so
`strategy_edge` answered `None` for minutes after every restart, and the slot
scorer treated "no answer" as "no edge". Fixed by persisting the cache and by
teaching the scorer that `unknown` is not `unproven` — an allocation evidence
already bought is held while the study is merely silent, though a realised
loser is still demoted, because that branch stands on its own evidence.

**Second cause, larger:** persisting it did not help, because the file went to
`/var/lib/matrix/models`, which is not a volume. Each container had a private
copy. `symbol_costs` had resolved to `~/.claude/matrix_models` — the shared
`matrix_claude_config` volume — and the two modules written later simply picked
a different default. The live registry had `momentum_xs` registered at 936 in
one container and absent in another, which quietly voids the one guarantee a
pre-registration makes.

**Fix:** `matrix_shared.model_store` resolves the path, every model uses it, a
test asserts all three land in one directory under three distinct names, and
the existing registries were merged by earliest registration.

**The lesson:** a constant that is duplicated will diverge, and the duplicate
that matters is the one nobody reads twice. Both failures here were invisible
from inside a single container: everything wrote successfully, every log line
said so, and the file simply was not the same file.

## 2026-09-20 — The index that fixed retention broke price lookups

Adding `ix_market_trades_ts` so retention could sweep in time order changed the
plan for a query that had nothing to do with retention:
`SELECT price FROM market_trades WHERE symbol = $1 ORDER BY trade_ts DESC
LIMIT n`, the feature path's latest-price lookup. The planner switched from the
composite `(symbol, trade_ts)` index, which answers both the predicate and the
ordering, to a backward scan of the new time index with a filter on symbol.

For a liquid symbol that is harmless. For a rare one — and with ~900 symbols
most are rare — it walks most of a 322M-row index looking for fifty rows. Three
such lookups were sitting at nine to twelve minutes on `DataFileRead`.

`ANALYZE` fixed it: with current statistics the planner picks the composite
index for rare symbols and the time index only where it is genuinely cheap. A
fresh liquid lookup measured 603 ms including client startup.
`autovacuum_analyze_scale_factor = 0.02` from migration 0040 keeps the
statistics current as the backlog drains and the distribution shifts.

**The lesson:** an index is not a local change. It is a new option offered to
the planner for *every* query touching that table, and the planner will take it
wherever its estimates say so. After adding one to a large table, re-check the
plans of the queries that were already fast — and ANALYZE, because a new index
with stale statistics is how a good plan becomes a bad one.

## 2026-09-20 — A units error in my own incident note

While writing up the retention work I reported that the database "grew 12.5 GB
in two hours" under the unthrottled drain. It did not. `pg_size_pretty` reports
GiB and I compared its 112 GB reading against a raw byte count converted as
decimal GB. The real figure is 112.0 → 116.0 GiB, of which 3.4 GiB is the index
that migration 0039 added deliberately: 0.5 GiB of unexplained growth in two
hours, not 12.5.

The conclusion survived — deleting faster than VACUUM reclaims does grow the
file, and throttling the drain was still right — but the number that justified
it was inflated roughly twentyfold, and a future session reading that note
would have over-reacted. Corrected in [[operations]].

**The lesson:** `pg_size_pretty` is GiB, `pg_database_size` is bytes, and a
delta computed across the two is wrong by 7%% per power of 1024. State the unit
in the note, and compute deltas from raw bytes on both ends.

## 2026-09-29 → 10-09 — Ten days without a trade, three causes stacked

All times UTC, measured 2026-10-09 from `predictions`/`outcomes`/`paper_positions`,
`docker logs`, `~/.orbstack/log/vmgr.1.log`, `log show` (powerd), `pmset -g log`.

**Symptom:** predictions/day fell from 700–1 000 (weekdays Sep 21–25) to
100–280 (Oct 1–6), then zero on Oct 7–8; on Oct 9 every container showed
"Up 5 minutes". The last paper position opened **2026-09-29**; nothing filled
again until 10-09. No alert reached the operator at any point.

**Three separate causes, in order:**

1. **Sep 20 → 29: the learning loop shrank the book (by design, but too far).**
   `grid` retired 09-20, `momentum_xs` 09-21 22:34 (no emissions after 22:27),
   every challenger 09-27…09-29 (dca v5, oi_breakout v3, matrix_agent v8, the
   four `bist_*` v2, funding_reversion v7, matrix_agent/us v2). Shadow
   predictions went 342 (09-25) → 113 (09-29) → 0 (10-01). In the champion
   wallet nearly every slot row is 0 (measured 10-09), so `backpressure.room()`
   caps each strategy at `BACKLOG_MIN = 3` open predictions and the paper
   engine's EV floor skipped the rest ("skipped N below 1.00x round-trip cost;
   opened 0"). Fills: 19 (09-25), 12, 15, 4, 5 (09-29), then none. The dips on
   09-26/27 and 10-03/04 are weekends (BIST and US closed), not decay.
2. **Sep 30 07:27 → Oct 6 14:21 (6.3 days): the VM lost internet; the host did not.**
   New outbound connections from every container failed from ~07:27 (Binance
   poll `HTTP error` 07:28, Telegram `Timed out` 07:42); the Bybit WebSocket,
   already established, kept streaming until 12:31 and then could never
   reconnect ("timed out during opening handshake" every 40 s for six days).
   News feeds, yfinance and the LLM (circuit breaker open) failed the same way.
   The host itself resolved DNS normally on 10-06, so it was OrbStack's network
   path, not Wi-Fi. OrbStack logged **nothing** at the moment it broke — its
   last `TCP forward` error was 06:28, then silence, and later only
   `DNS query failed name=api.telegram.org`. Root cause inside OrbStack is
   unknown; a VM restart (the power loss) cleared it. Meanwhile strategies kept
   emitting on frozen prices — dca 72/day, BIST 144/day, matrix_agent on its
   rule fallback — which is why the count decayed instead of hitting zero.
   notify detected it within minutes and produced **783 alerts (145 URGENT)**,
   every one `pushed … to 0/1 chats`: the alert channel shared the VM's dead
   network. The stall watchdog restarted nothing — every service kept logging
   warnings, which is all it measured.
3. **Oct 6 14:21 → Oct 9 12:01 (2.9 days): the laptop ran its battery flat.**
   On battery from at least 09:05 (100 %), draining ~19 %/h under the stack's
   load; macOS posted the low-battery warning at 13:45 (10 %) and 2 % at
   14:11; the last container log line is 14:21. Postgres confirmed it on
   restart: "database system was not properly shut down; automatic recovery"
   (2–4 s, clean). AC came back and the Mac booted at **09:01 on 10-09**, then
   sat at the login window for three hours (no automatic login): LaunchAgents and
   OrbStack start only at login, which happened at 12:00; containers at 12:01.

**What changed (2026-10-09):**
- `scripts/stall_watchdog.sh` (launchd, every 5 min) now alerts on Telegram
  **from the host**, which kept its network through cause 2: on battery
  (re-alert 30 min, every 5 min under 25 %), watchdog gap / reboot (post-hoc
  "Matrix was down Xh"), Docker unreachable (starts OrbStack if it is not
  running), containers without egress while the host has it, BTCUSDT ticker
  older than 30 min, host disk under 30 GiB. Failed sends queue and retry.
  Verified end to end: test message and a real on-battery alert delivered.
  `MATRIX_WATCHDOG_HEAL_EGRESS=1` opts into `orb restart docker` after 30 min
  of dead egress; off by default because the fix is plausible, not proven.
- notify: counts alerts that reached nobody and says so on the first delivery
  that works; one log line per failure instead of a traceback; new `no_fills`
  detector (no paper position opened for 24 h) — the condition that held for
  ten days while every freshness signal was green; ticker probe pinned to
  `exchange='bybit'`.

**Not fixed here (operator / other tracks):** keep the laptop on AC — at the
time of writing (10-09 11:59) it had been unplugged again; nothing local can
alert once the host is dead, only before. ~~Strategies emit on stale prices
instead of standing down.~~ Fixed the same day in 7216361: a draft persists
only when its symbol's newest bar or ticker is fresh (crypto 3 min). Slots at 0 + EV floor mean the system can be fully up
and still never trade — that is a learning-loop question, not uptime.

**The lesson:** an alert path that shares the failure domain of what it
watches is not an alert path. Three layers of liveness checks existed and all
of them worked; none of them could speak, because they all sat behind one NAT.


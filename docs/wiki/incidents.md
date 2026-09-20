---
title: Incidents
updated: 2026-09-19
sources: [docs/CHANGES.md, git log, "~/.orbstack/log/vmgr.log", "pmset -g log"]
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
  one day. Fixed in 74eb6ff; the −346.76 refund is still pending
  ([[pnl-reality]]). All champion/challenger comparisons before this date are
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

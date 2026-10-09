---
title: Operations
updated: 2026-10-09
sources: [Makefile, docs/OPS_HARDENING.md, scripts/, scripts/stall_watchdog.sh]
status: current
---

Running the system day to day. The host-level dependencies (lid sleep, VM
memory, launchd) are in the global wiki's machine page.

## Claims
- **Start / stop**: `make up-dev` (compose base + dev + limits overlays),
  `make down`, `make logs`, `make ps`, `make stats`. Single service:
  `docker compose ... up -d --no-deps <svc>` — without `--no-deps` compose
  recreates Postgres too.
- **Daily health, in one look**: age of the newest ticker snapshot, newest 1m
  bar, newest prediction, newest wallet snapshot, newest outcome. All five
  should be seconds-to-minutes old. Container state is *not* a health signal —
  see [[incidents]].
- **Money**: `make reset-capital ASSET=crypto [WALLET=default] [AMOUNT=+346.76]`
  adjusts wallet capital without touching learning data (`db-reset` truncates
  outcomes and must not be used for this). Used 2026-09-19 to refund
  +346.76 for the 2026-09-13 shadow-wallet bug ([[pnl-reality]]).
- **Risk**: `make circuit-reset ASSET=crypto`, Telegram `/circuit_reset`.
- **Learning**: `make method-ab`, `make ranker-ab`, `make leaderboard`,
  `make lab-scan`, `make director-once` / `director-tail` / `director-digest`.
- **Dev agent**: `make dev-agent-queue`, `make dev-agent-clean`, and from
  Telegram `/dev_tasks`, `/dev_accept <id>`, `/dev_discard <id>`,
  `/dev_revise <id> <notes>` — accept performs the real merge.
- **LLM**: `make llm-status`, `make claude-creds-sync`, `make claude-creds-install`.
  See [[llm-stack]].
- **Data**: `make retention-drain`, then `make db-compact TABLE=market_trades`
  to return disk to the OS. `make backup-now`; the sidecar dumps daily with
  14-day retention.
- **Knobs worth knowing**: `MATRIX_BACKLOG_SLOTS_MULT` (emission cap),
  `MATRIX_AGENT_UNIVERSE_CAP`, `MATRIX_AGENT_HOLD_COOLDOWN_S`,
  `MATRIX_AGENT_BATCH_CHUNK`, `MATRIX_LLM_DAILY_BUDGET_USD`,
  `MATRIX_LLM_CALL_TIMEOUT_S`, `MATRIX_SLOT_MIN_N`, `MATRIX_CHALLENGER_MODE`,
  `MATRIX_CERT_*`, `MATRIX_WATCH_SUPERVISE`.
- **Telegram**: configured since 2026-09-20 (rotated that day, see
  [[incidents]]); delivery verified 2026-10-09 from both paths — notify
  (`pushed … to 1/1 chats`) and the host watchdog (`make watchdog-test`).
  `OPENROUTER_API_KEY` is still unset.

## Host watchdog — the alert path that survives the VM

`make watchdog-install` loads two LaunchAgents: `com.matrix.caffeinate`
(`caffeinate -s`) and `com.matrix.watchdog`, which runs
`~/Library/Application Support/matrix/stall_watchdog.sh` every 5 minutes (a
copy — launchd cannot execute or read anything under `~/Documents`, so the two
Telegram keys are copied to `watchdog.env` there, mode 0600;
`scripts/rotate_telegram_token.sh` refreshes it). Re-run the install after
editing the script. Log: `~/Library/Logs/matrix-watchdog.log`; state:
`…/matrix/state/`. `make watchdog-status`, `make watchdog-test`.

It exists because notify lives inside the VM and dies with it: 2026-09-30 →
10-06 notify produced 783 alerts and delivered none ([[incidents]]). What the
host watchdog checks, alerting edge-triggered and queueing sends that fail:

| check | fires when | action |
|---|---|---|
| power | host on battery | alert every 30 min, every 5 min under 25 % |
| gap | watchdog did not run for 15 min+ | one post-hoc "down Xh" message, says if the host rebooted |
| docker | `docker info` fails for 30 s | starts OrbStack if not running, else alert |
| egress | 2 probes in a row: containers cannot reach Bybit/Telegram/Binance, host can | alert; `orb restart docker` only with `MATRIX_WATCHDOG_HEAL_EGRESS=1` |
| data | newest bybit BTCUSDT ticker > 30 min | alert (notify alerts at 5 min when it can) |
| disk | host free < 30 GiB | alert |
| stall | a loop service's last log line exceeds its budget | `docker restart <svc>` |

What no local check can do: report a host that is already off. The gap message
arrives only after it comes back, and after a power loss nothing runs until
someone logs in (automatic login is off; FileVault is off, so it can be enabled). Keep the machine on AC; the battery alert is
the only warning there will be.

## Storage

The local database is the thing that will fill the disk, and `market_trades`
is nearly all of it. Retention lives in `matrix_shared/retention.py`, runs
inside `bars-aggregator` every 5 minutes, and keeps raw prints 7 days, L2
snapshots 2 days, ticker snapshots 30 days, wallet snapshots 30 days.

`market_trades` is swept globally in time order through `ix_market_trades_ts`;
the other tables are swept per symbol, which is right for them because they are
small. Check that retention is actually working, not merely running (2026-09-20
it ran for months and deleted almost nothing — see [[incidents]]):

```sql
-- The progress metric is the GLOBAL frontier, not one symbol's: the sweep
-- deletes oldest-first across all symbols, so a single symbol's minimum sits
-- still until its whole day is cleared. Cheap now that (trade_ts) is indexed.
SELECT min(trade_ts) FROM market_trades;   -- should be within the policy window

-- how much of the backlog is left
SELECT count(*) FROM market_trades WHERE trade_ts < now() - interval '7 days';
```

Deleting rows does not shrink the file. Space goes to Postgres' free-space map,
and only VACUUM puts it there, so autovacuum has to keep pace with retention or
the table extends on disk while rows disappear. `market_trades` carries
`autovacuum_vacuum_scale_factor = 0.02` (migration 0040) for exactly that
reason: the default 0.2 means waiting for ~64M dead tuples on a 322M-row table,
and autovacuum had never run on it at all.

```sql
-- has autovacuum ever touched it, and how far behind is it?
SELECT relname, n_dead_tup, last_autovacuum, autovacuum_count
FROM pg_stat_user_tables WHERE relname = 'market_trades';
```

**Deleting faster than VACUUM can reclaim makes the file grow, not shrink.**
A deleted row becomes a dead tuple and still occupies its page until VACUUM
returns it to the free-space map, so a drain that outruns autovacuum makes the
file grow instead of shrink. Measured on 2026-09-20, with the numbers stated
carefully because the first version of this note got them wrong by comparing
decimal GB against a GiB reading: the database went 112.0 → 116.0 GiB over two
hours, of which **3.4 GiB is the new `(trade_ts)` index**. Non-index growth was
0.5 GiB in two hours, about 6.5 GiB/day against a 1.56 GiB/day baseline — real,
but a fraction of the "12.5 GB in two hours" the bad arithmetic suggested.

The sustainable shape is a drain that outpaces ingestion by a few times, with
autovacuum given room to run:

| knob | value | why |
|---|---|---|
| `MATRIX_RETENTION_BUDGET_S` | 15 | ~20M rows/day, about 5x the inflow |
| `autovacuum_vacuum_scale_factor` | 0.02 | wakes at ~6.5M dead, not ~64M |

A full autovacuum pass on this table measured ~4 hours at the default throttle
and reclaims far more than a pass-worth of deletions creates, so the two
balance. Watch `pg_stat_progress_vacuum` and `pg_database_size` together — the
number that matters is whether the file has stopped growing, not how many rows
retention reports.

`make retention-drain` forces a full catch-up; `make db-compact TABLE=…` runs
VACUUM FULL to return space to the OS and **locks the table** while it does.

**Measured 2026-10-09 — the drain finished, the indexes did not shrink.**
`market_trades` is down to 18.3M rows (`reltuples`) and a **5.2 GB heap**
(from ~49 GB; vacuum truncated the emptied tail), oldest row 2026-09-30 00:16
— inside the policy window once the outage gap is counted. But its four
B-tree indexes are still **54 GiB** (exchange_id 29, pkey 13, symbol_ts 8.3,
ts 3.5): a B-tree only returns pages through REINDEX, so they are ten times
the size of the table they index, every autovacuum pass walks all of them,
and the feature path reads them through 128 MB of `shared_buffers`. Database
84.5 GiB (90.8e9 bytes, from 116 GiB on 09-20); VM volume 190 GiB free of 591;
host 198 GiB free. Runway is no longer a concern. The stats were reset by the
unclean shutdown, so `last_autovacuum` reads NULL — not evidence that it never
ran. Recommendation: `REINDEX INDEX CONCURRENTLY` each of the four, smallest
first (no write lock; needs a deliberate operator run — the session's
permission policy refused it as a shared-resource change).

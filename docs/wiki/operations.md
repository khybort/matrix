---
title: Operations
updated: 2026-09-19
sources: [Makefile, docs/OPS_HARDENING.md, scripts/]
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
- **Unconfigured as of 2026-09-19**: `TELEGRAM_BOT_TOKEN` (notify runs dry-run,
  alerts only reach the logs) and `OPENROUTER_API_KEY`.

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
-- should be within the policy window, not months ago
SELECT min(trade_ts) FROM market_trades WHERE symbol = 'BTCUSDT';
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
returns it to the free-space map. On 2026-09-20 the first, unthrottled version
of the time-ordered sweep deleted ~155M rows/day while autovacuum had not
finished a single pass, and the database grew 12.5 GB in two hours — the
opposite of the intent. The sustainable shape is a drain that outpaces
ingestion by a few times, not by thirty, with autovacuum given room to run:

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

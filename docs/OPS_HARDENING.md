# Operations hardening — crash recovery & disk hygiene

Living playbook for the failure modes we've already hit, plus the
preventive defaults baked into the stack.

## Preventive defaults (already in place)

| Surface | Guard | Where |
|---|---|---|
| Container log files | json-file, `max-size 10m`, `max-file 3` (≈30 MB cap per container) | `docker-compose.yml` `x-logging` anchor |
| Postgres autorecovery | `restart: unless-stopped` on every service | base compose |
| Postgres health gating | `pg_isready` healthcheck; dependents `depends_on … service_healthy` | base compose |
| Risk caps | Hard caps in `wallets` table, never mutated by code | `docs/TRADING.md` |
| Builds | `make build` cached; `make migrate` always rebuilds migrate image first | `Makefile` |
| Retention | `market_trades` 7d, `market_orderbook_snapshots` 2d, `market_ticker_snapshots` 30d, `wallet_snapshots` 30d — pruned in bounded batches every 5 min by `bars-aggregator` (`MATRIX_RETENTION_*_DAYS`, `_BUDGET_S`, `_BATCH`, `_ENABLED`) | `matrix_shared/retention.py` |
| Equity curve cadence | `wallet_snapshots` written at most every 60s (`WALLET_SNAPSHOT_INTERVAL_S`); circuit checks still every tick | `backtest/paper_trade.py` |
| Backups | `backup` sidecar: `pg_dump -Fc` both tiers every 24h → `./backups/<ts>/`, market stream tables schema-only, 14-day prune (`BACKUP_INTERVAL_H`, `BACKUP_KEEP_DAYS`) | `infra/db/backup.sh` |
| Liveness alerts | `notify` always on; stalls in paper engine / signals / ingestion / bars, disk pressure, LLM rule-only, dev_agent failures → Telegram (or logs in dry-run) | `services/notify/health.py` |

## Daily / weekly habits

Backups and retention run unattended (see table above). What is still worth
a human glance:

```bash
make disk            # docker system df — eyeball it
make backup-now      # extra backup before risky work (the sidecar does daily)
make ps              # are all expected containers up?
make stats           # row counts per major table
```

Disk is only returned to the OS by `VACUUM FULL`; retention alone stops the
growth. After the first drain of a huge table: `make db-compact TABLE=market_trades`
(locks the table for the duration — run off-hours). `make retention-drain`
forces the whole backlog through now instead of the 5-minute trickle.

## Autonomy operations (2026-09-13)

The system runs and improves itself; these are the few operator touchpoints
that remain (docs/AUTONOMY_PLAN.md):

| Command | When |
|---|---|
| `make director-tail` / `make director-once` / `make director-digest` | Read the hourly Director brief, force a review, or dump the digest |
| `make method-ab [DAYS=7]` | Realised PnL by decision method (rule / llm / llm+rule / conflict) |
| `make circuit-reset ASSET=crypto [WALLET=default]` or Telegram `/circuit_reset crypto` | Re-arm a tripped daily-loss circuit while `LIVE_EXECUTION_ENABLED=true` (paper mode re-arms itself at the UTC day roll) |
| Telegram free text: "stop trading DOGE" | Brain stores an operator directive (`remember_directive`); every agent honours it immediately |
| `make llm-subscription` / `make llm-openrouter` / `make llm-status` | LLM backend. Subscription needs `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`); OpenRouter needs `OPENROUTER_API_KEY`. Plans fall back subscription → openrouter → rule-only |
| `make openrouter-models` | Current free OpenRouter models (tool-calling first) when a default vanishes |
| `make retention-drain` / `make db-compact TABLE=market_trades` | Force the retention backlog through / return disk to the OS (locks the table) |
| `make backup-now` | Extra backup before risky work (sidecar dumps daily) |
| `make dev-agent-queue` / `/dev-task accept <id>` | Only for tasks left `awaiting_review` (dirty tree or merge conflict); tests+merge are automatic otherwise |

## Failure modes & recovery

### 1. "No space left on device" → Postgres panic

**Signal:** Postgres logs "could not write file … No space left on device",
the container dies with exit 6, dependent services start throwing
ConnectionDoesNotExistError.

**Recovery:**
```bash
make disk                # confirm the squeeze
make disk-clean          # reclaim builder cache + dangling images
docker system df         # verify recovered
make up-dev-local        # bring stack back up
make migrate             # WAL recovery may have rolled back DDL
```

If the data dir is actually corrupted (rare — usually WAL replay
handles it):
```bash
docker volume rm matrix_pgdata           # nukes LOCAL data
make up-dev-local
make migrate
# shared tier survives because postgres-shared is a separate volume
```

**Prevent:** `make disk` once per session, before any heavy iteration.

### 2. "endpoint with name … already exists in network"

**Signal:** `make up-dev` fails because the docker network has a stale
endpoint reservation (usually post-crash).

**Recovery:**
```bash
docker ps -aq --filter "name=matrix-" | xargs -r docker rm -f
docker network rm matrix-net
# if rm still fails, restart the daemon:
orb restart --all      # OrbStack
# or: systemctl restart docker (Linux)
make up-dev-local
```

### 3. "Module not found: '@/lib/db'" on the dashboard

**Signal:** Next.js 500s every API call, web logs "Module not found"
referring to a TS path alias.

**Cause:** `tsconfig.json` not bind-mounted into the dev container.

**Fix:** check `docker-compose.dev.yml` web volumes include
tsconfig.json, next-env.d.ts, postcss.config.js. Rebuild dev image:
```bash
make build-dev
make down && make up-dev-local
```

### 4. Migration silently does nothing

**Signal:** `make migrate` prints "Context impl" and exits without
"Running upgrade X -> Y".

**Cause:** The migrate image is older than the new revision file in
`infra/db/alembic/versions/`. Image was baked when only 0006 existed,
new 0007 file isn't inside.

**Fix:** the modern `make migrate` target rebuilds the migrate image
first. If the rebuild was skipped (older Make file or local edit),
force it:
```bash
docker compose -f docker-compose.yml --profile migrate build migrate
make migrate
```

### 5. asyncpg refuses Neon URL

**Signal:** `connect() got an unexpected keyword argument 'sslmode'`.

**Cause:** Neon ships URLs with libpq `?sslmode=require&channel_binding=require`
query params; asyncpg ignores those and treats them as connect kwargs.

**Fix:** Already handled by `packages/python-shared/config.py:_normalize()`
which strips them and `db.py:_create_engine()` which adds
`ssl="require"` for managed DBs. If you ever bypass these helpers
(e.g. raw `asyncpg.connect(url)` in a script), strip the query string
yourself or pass `ssl="require"` explicitly.

### 6. Disk full from runaway logs

**Signal:** Container journal files in `/var/lib/docker/containers/…/`
grow into GBs.

**Prevented by:** the json-file driver cap. Verify on a running
container:
```bash
docker inspect matrix-postgres --format='{{json .HostConfig.LogConfig}}'
# should show {"Type":"json-file","Config":{"max-file":"3","max-size":"10m"}}
```

If a service is logging too loudly, address the source (it's almost
always an exception loop). Don't try to fix it by raising the cap.

## Backups

`make backup` writes two SQL dumps (LOCAL + SHARED-local) to
`./backups/<timestamp>/`. The backups dir is gitignored.

To restore:
```bash
make backup-list
make restore-local FILE=backups/20260524-150000/local.sql
make restore-shared FILE=backups/20260524-150000/shared.sql
```

Restore drops + recreates the target DB. Take a fresh `make backup`
first if you're unsure.

For multi-PC setups with real Neon as the SHARED tier: Neon does its
own continuous backups; you only need `make backup` for the local
postgres. The `shared.sql` line in `make backup` is harmless (skips
when postgres-shared isn't running).

## What we do NOT defend against (yet)

- WAL bloat on an idle Postgres: vacuum is on by default; no separate
  monitoring. If you leave the stack running for weeks unattended, watch
  `pg_database_size('matrix')` grow.
- Cross-host disk failure: there is no replication. The graph_signals
  flush is the only multi-PC redundancy path; the rest is single-host.
- Service-level health probes for the Python loops. They restart
  cleanly via `restart: unless-stopped` if they actually crash, but a
  silently wedged asyncio task won't trigger that. Symptom: counters in
  `make stats` stop advancing — easy to spot on the dashboard.

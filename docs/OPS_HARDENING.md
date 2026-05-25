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

## Daily / weekly habits

```bash
make disk            # docker system df — eyeball it
make backup          # pg_dumpall both DBs to ./backups/<ts>/
make ps              # are all expected containers up?
make stats           # row counts per major table
```

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

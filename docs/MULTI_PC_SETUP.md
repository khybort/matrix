# Multi-PC setup

> Run the same repo on two (or N) PCs that share state through a common Neon Postgres while keeping a fast local Postgres for hot-path data and the AGE context graph.

## Storage tiers (recap)

| Tier | Where it lives | What's in it | Why |
|---|---|---|---|
| **LOCAL** | Each PC's docker postgres (AGE + pgvector) | `market_trades`, `market_orderbook_snapshots`, `market_ticker_snapshots`, `raw_documents`, `matrix_graph` (AGE) | High write volume, location-specific, graph traversal needs to be sub-ms |
| **SHARED** | One Neon project, all PCs connect to it | `wallets`, `paper_positions`, `predictions`, `outcomes`, `wallet_snapshots`, `strategy_configs`, `mutation_proposals`, `lab_experiments`, `lab_evaluations`, `graph_signals` (future) | Cross-PC coordination, single source of truth for state-of-the-world |

Single-PC users can leave `SHARED_DATABASE_URL` blank — it falls back to the local Postgres. Switch to multi-PC by changing one env var.

## Single-PC workflow — fully offline (default)

The default `.env` brings up **two local Postgres instances**: `postgres`
(LOCAL tier — market data + AGE graph) and `postgres-shared` (a local
stand-in for Neon — wallet, predictions, lab, graph_signals). Internet
is not required.

```bash
cp .env.example .env
# edit .env: at minimum, set NODE_ID

make build                  # build all images (~5 min first time)
make up-dev-local           # 8 services + postgres + postgres-shared
make migrate                # migrate LOCAL tier
make migrate-local-shared   # migrate the fake-Neon tier
make dashboard              # opens http://localhost:3030

make stats                  # LOCAL counts
make psql                   # psql shell to LOCAL
make psql-shared            # psql shell to the fake-Neon (port 5433)
make logs                   # tail everything
```

Hot reload: edit any `.py` file in `services/*/src/` or `packages/python-shared/src/` and the relevant service restarts in a few seconds. Edit any `.tsx` in `apps/web/src/` and Next.js HMR updates in the browser.

### Switching to real Neon later

When you want a multi-PC setup, swap one env var:

1. In `.env`: comment the `postgres-shared` line, uncomment the Neon line
2. `make down && make up-dev` (no `-local`)
3. `make migrate-shared` to ensure Neon's schema is current

The same data flows; only `SHARED_DATABASE_URL` changed.

## Multi-PC workflow

### 1. One-time: provision Neon

1. Create a Neon project (https://console.neon.tech)
2. Get the connection string (looks like `postgres://user:pass@ep-xxx.neon.tech/main`)
3. Enable `pgvector` extension on the Neon side (Neon dashboard → Extensions)
4. Run migrations *into Neon* from one PC:
   ```bash
   SHARED_DATABASE_URL=<neon-url> make migrate
   ```
   The migrate container will create all shared-tier tables on Neon.

### 2. Per-PC: configure env

On each PC, in `.env`:

```bash
# Unique node id (mac1, mac2, work-tower, etc.)
NODE_ID=mac1

# Each PC has its own local Postgres for hot data + AGE graph
LOCAL_DATABASE_URL=postgres://matrix:matrix_dev_only@postgres:5432/matrix

# Everyone shares this Neon
SHARED_DATABASE_URL=postgres://user:pass@ep-xxx.neon.tech/main

# Role split per PC. e.g. mac1 might focus on ingestion/agent,
# mac2 on labs (CPU intensive)
NODE_ROLES=ingestion,graph,agent,backtest
```

### 3. Bring up each PC

```bash
make build
make up-dev
```

Each PC runs the services in its `NODE_ROLES` (TODO: compose still spawns
all services; selective startup is on the roadmap). For now, comment out
unwanted services in `docker-compose.yml`, or use `docker compose up <svc>`.

### 4. Dashboard

The dashboard reads from `SHARED_DATABASE_URL`. Open `http://<any-pc>:3030`
and you see everything from every PC.

For internet access to the dashboard, expose port 3030 via Tailscale or a
small reverse-proxy. Don't expose it to the open internet without auth.

## Operational notes

### Migrations

Schema changes go to BOTH tiers. The migration runner connects to whichever
DB its env var points to. For multi-PC:

```bash
# Update LOCAL on each PC (when local tables change)
make migrate

# Update SHARED once (anyone can run this)
SHARED_DATABASE_URL=<neon-url> make migrate
```

(A future improvement: tag each migration with `tier=local|shared` so the
runner picks the right target.)

### Conflict cases

- **Single wallet, multiple PCs**: agent instances on each PC ALL submit
  predictions to the same `wallets`. The paper-trade engine on each PC
  picks up any open prediction whose symbol it can price locally. Risk
  caps are enforced server-side (DB row), so the total open exposure
  across PCs is bounded by `max_concurrent_positions` and
  `max_position_pct`. There is no double-spend because the engine uses
  `FOR UPDATE SKIP LOCKED` semantics on prediction claim.

- **Lab gene pool**: all PCs evolve the same `lab_experiments`. Each PC's
  labs container scores its own evaluations into the shared table. More
  PCs → faster fitness convergence.

- **Graph divergence**: each PC's local AGE may diverge if their RSS
  feeds differ. The future `graph_signals` aggregate table will let the
  agent on PC-A consult PC-B's recent graph context as a fallback.

### Failure modes

- If a PC goes offline, its in-flight `lab_evaluations(status='open')`
  stay open and eventually marked `stale`. Other PCs' work is unaffected.
- If Neon is unreachable, services that need shared-tier writes will
  error. Recommendation: monitor Neon connection health on each PC; the
  dashboard will show stale data when Neon drops.

### Performance

- Single-PC: hot-path queries (market_trades read for paper-trade pricing)
  stay local — full speed.
- Multi-PC: `predictions`/`paper_positions` writes go to Neon. Add ~30-100ms
  latency per write. Mitigation: batched writes and connection pooling
  (already in place via SQLAlchemy async engine).
- Graph traversal: stays LOCAL on the queriving PC. No network cost.

## Verifying multi-PC

Quick smoketest after both PCs are up:

```bash
# On PC A:
make stats   # see counts including the shared tables

# On PC B (right after):
make stats   # same counts for shared tables, different for local-only
```

`lab_experiments`, `predictions`, `outcomes`, `wallet_snapshots` should match
across PCs. `market_trades` and `raw_documents` will differ — that's the
intended tier split.

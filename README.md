# Matrix

> Self-learning, context-graph-driven AI research engine for multi-market financial intelligence and personal cognitive augmentation.

## What this is

Matrix continuously ingests financial market data, news, filings, and on-chain signals across multiple markets (US equities + options, crypto, BIST, FX). It builds a **persistent context graph** of entities and relationships, scores its own past predictions against actual outcomes, and uses that signal to **auto-tune its own analysis strategies**.

Two products emerge from the same engine:

1. **Internal**: a "second brain" you can query — sub-second graph-augmented answers across the entire ingested corpus.
2. **External**: a paid research bulletin product, with a public free tier for audience-building.

## Why this exists

Most retail/quant research products are static reports written by humans, or shallow LLM wrappers. Matrix differs:

- **Self-updating context graph** — every ingestion extracts entities/relationships and updates the graph. Knowledge compounds.
- **Outcome-scored learning** — predictions are tracked against later reality; strategies and prompts that work survive, others mutate or die.
- **Market-agnostic** — pluggable connectors mean a new market is a 1-day plug, not a re-architecture.
- **Asset, not service** — the graph + track record become the moat.

## Status

Greenfield. Currently bootstrapping the foundation.

## Structure

```
matrix/
├── apps/
│   └── web/                  # Next.js dashboard + public bulletin site (Machine B)
├── services/
│   ├── ingestion/            # Market data + news + filing ingestion (Machine A)
│   ├── graph/                # Entity extraction + context-graph upsert (Machine A)
│   ├── strategy/             # Analysis + signal generation agents (Machine A)
│   └── backtest/             # Backtest + paper-trade + scoring loop (Machine A)
├── packages/
│   ├── ts-shared/            # Shared TS types/utils (Machine B)
│   └── python-shared/        # Shared Python models/utils (Machine A)
├── infra/
│   └── db/                   # Migrations, schema, seed
├── docs/
│   ├── VISION.md
│   ├── ARCHITECTURE.md
│   ├── ROADMAP.md
│   ├── WORK_SPLIT.md
│   └── CHANGES.md
└── CLAUDE.md
```

## Roadmap (high level)

- **W1-2**: Foundation, schema, first market connector
- **W3-6**: Graph extraction loop, first strategy hypothesis
- **W7-10**: Paper-trade engine, daily report generator
- **W11-16**: Public site + free bulletin tier, content seeding
- **W17-26**: Paid tier launch, optimize for first $1k MRR

Full milestones: [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Local dashboard

On the machine running Docker (OrbStack):

- **https://matrix.local** — OrbStack proxy (default dev URL)
- **`make dashboard`** — opens `matrix.local` in the browser

Stack: `make up-dev` or `make up-dev-local` (offline shared DB). See [`CLAUDE.md`](CLAUDE.md) for full commands.

## Public dashboard (internet)

The web UI has **no built-in authentication**. Only expose it if you accept that anyone with the URL can read wallet/PnL/strategy data. Do **not** port-forward Postgres (`5432` / `5433`) or internal APIs — only **TCP 3030**.

### 1. Bind web to the host

```bash
make up-dev-public    # dev + hot reload
# or
make up-prod-public   # prod images
```

This applies [`docker-compose.public.yml`](docker-compose.public.yml) and listens on **`0.0.0.0:3030`**.

### 2. Router

Forward **WAN TCP 3030** → this Mac’s LAN IP. Allow incoming 3030 in macOS Firewall. If your ISP uses **CGNAT**, port forwarding will not work from the open internet; use a tunnel (e.g. Cloudflare) instead.

### 3. Free DNS

**No signup (follows your public IP):**

```bash
make public-ip
```

Example output shape:

```text
Dashboard:  http://<public-ip>:3030
sslip.io:   http://<a-b-c-d>.sslip.io:3030
```

Re-run after your home IP changes.

**DuckDNS (stable hostname):**

1. Create a subdomain at [duckdns.org](https://www.duckdns.org).
2. In `.env` (gitignored):

   ```bash
   DUCKDNS_SUBDOMAIN=matrix-local
   DUCKDNS_TOKEN=<your-token>
   ```

3. Point DNS at this machine:

   ```bash
   make duckdns-update
   ```

4. Optional cron (every 5 min) so IP updates propagate:

   ```bash
   */5 * * * * cd /path/to/matrix && ./scripts/duckdns-update.sh -q
   ```

Dashboard URL: **`http://<subdomain>.duckdns.org:3030`**

To bind only on localhost (e.g. Tailscale `serve` in front), set in `.env`:

```bash
MATRIX_PUBLIC_WEB_BIND=127.0.0.1:3030
```

More context: [`docs/MULTI_PC_SETUP.md`](docs/MULTI_PC_SETUP.md) (Tailscale note).

### This node — public URLs (2026-06-02)

| URL | Role |
|-----|------|
| http://matrix-local.duckdns.org:3030 | DuckDNS (stable; run `make duckdns-update` after IP change) |
| http://95.70.152.111:3030 | Current public IPv4 |
| http://95-70-152-111.sslip.io:3030 | sslip.io alias for the same IP |

Refresh IP / sslip.io: `make public-ip`.

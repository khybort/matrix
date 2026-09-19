---
title: Services
updated: 2026-09-19
sources: [docker-compose.yml, docker-compose.dev.yml, docker-compose.limits.yml]
status: current
---

21 containers. Dev overlay bind-mounts `src/` and runs each service under a
supervising watcher ([[development]]).

## Claims
- **Data in**
  - `ingestion-market` — Bybit WebSocket (crypto, mainnet data), yfinance
    pollers for BIST and US, symbol discovery. Started with
    `--markets crypto bist us`.
  - `ingestion-news` — RSS feeds every 300 s into `raw_documents`.
  - `bars-aggregator` — 1m bars from trades each minute, 1h rollups, and owns
    data retention (7-day trade window).
- **Understanding**
  - `graph` — extracts entities/relations into the AGE graph, publishes
    `graph_signals`.
  - `agent` — the LLM strategy (`matrix_agent`): features → rule verdict → LLM
    verdict → blend → prediction, every 15 s.
  - `strategy` — 12 deterministic modules, every ~30 s.
  - `agent-lessons` — distils outcomes into `avoid`/`prefer` lessons.
  - `synthesis`, `brain` — narrative synthesis and the operator-facing Q&A agent.
- **Execution and measurement**
  - `backtest` — the paper engine, 5 s loop ([[paper-engine]]).
  - `backtest-api` — read API for the dashboard.
  - `execution` — live-order gateway; idles behind [[risk-gates]], never sent an order.
- **Improvement**
  - `reflection` — metrics → mutation proposals, efficacy, certificates, slot scoring (600 s).
  - `labs` — genetic search over parameters, promotion scanning, auto-apply (20/300/600 s).
  - `director` — hourly LLM review of the whole system with a small write toolbelt.
  - `dev_agent` — writes and merges its own code changes in a worktree.
- **Operations**
  - `notify` — Telegram bot and liveness/PnL/budget alerts (token unset → dry-run to logs).
  - `backup` — daily `pg_dump` sidecar, 14-day retention.
  - `web` — Next.js dashboard on :3030.
  - `postgres`, `postgres-shared` — the two tiers.
- **Resource caps** live in `docker-compose.limits.yml`: CPU per tier and, since
  2026-09-13, memory per tier (db 2 g, heavy 1 g, worker 768 m, light 512 m) so
  one runaway service is OOM-killed instead of wedging the VM.

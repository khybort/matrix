---
title: Architecture
updated: 2026-09-19
sources: [docker-compose.yml, docs/ARCHITECTURE.md, packages/python-shared/src/matrix_shared/]
status: current
---

Python 3.13 monorepo: one shared library plus ~20 single-purpose services, all
in Docker on one host. No cloud. Services never call each other over HTTP for
data; they meet in Postgres.

## Claims
- **Two database tiers, and the split matters.**
  - `matrix` (LOCAL, port 5432) — raw market data, the Apache AGE knowledge
    graph, the dev_agent's own tables. Big and disposable.
  - `matrix_shared` (SHARED, port 5433) — predictions, outcomes, wallets,
    positions, strategy configs, proposals, lessons, certificates. The system's
    memory of what it decided and what happened.
  - Reading the wrong tier is a recurring bug class: some tables exist on both
    (`dev_tasks`, `strategy_configs`, `wallets`) and the empty copy answers
    without error. See [[data-model]].
- **Data flow, one lap:**
  `ingestion-market` (Bybit WS, yfinance) → `market_trades`/`market_ticker_snapshots`/`market_orderbook_snapshots` (LOCAL)
  → `bars-aggregator` → `market_bars` 1m and 1h
  → `agent` (LLM+rule per symbol) and `strategy` (12 rule modules) → `predictions` (SHARED)
  → `backtest` paper engine → `paper_positions` → `outcomes`
  → `reflection` / `labs` / `agent_lessons` → `mutation_proposals` → applied as new `strategy_configs`
  → back into the next tick. [[learning-loop]] describes the gates on that last arc.
- **Three markets** share one codebase through a market adapter registry
  (`matrix_shared/markets/`): `crypto` (Bybit, 24/7, shorts allowed),
  `bist` (Borsa Istanbul, long-only), `us` (S&P 500 + Nasdaq-100, added
  2026-09-14). Session hours, fees and short permission come from the adapter.
- **The LLM is a component, not the system.** Rule models produce a verdict
  independently; the model's verdict is blended with it ([[strategies]]), and
  every LLM path degrades to rules when the backend is down ([[llm-stack]]).

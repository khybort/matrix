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

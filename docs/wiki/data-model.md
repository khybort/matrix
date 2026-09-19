---
title: Data model
updated: 2026-09-19
sources: ["\\d on both tiers", infra/db/alembic/versions/]
status: current
---

Which table lives where, and the traps in that split.

## Claims
- **LOCAL (`matrix`)** — `market_trades` (284 M rows, ~90 GB, 7-day retention
  draining), `market_ticker_snapshots`, `market_orderbook_snapshots`,
  `market_bars` (LIST-partitioned by asset class: `_crypto`, `_bist`, `_us`,
  `_other`), `raw_documents`, `bist_symbols`, `us_symbols`, `screener_*`, the
  AGE graph `matrix_graph`, and all `dev_*` tables.
- **SHARED (`matrix_shared`)** — `predictions`, `outcomes`, `paper_positions`,
  `wallets`, `wallet_snapshots`, `strategy_configs`, `strategy_slot_configs`,
  `mutation_proposals`, `agent_lessons`, `paper_trade_certificate`,
  `lab_experiments`, `lab_evaluations`, `tradable_symbols`, `graph_signals`.
- **Tables that exist on both tiers are a hazard.** `dev_tasks`,
  `strategy_configs` and `wallets` were created on both by migrations; the
  owning service writes one copy and a probe reading the other gets an empty,
  error-free answer. This silently disabled dev_agent alerting and stranded a
  Director-filed task (2026-09-13). Rule: use the tier of the owning service.
- **`predictions.context` and `mutation_proposals.metrics_window` are `json`,
  not `jsonb`** — the `?` operator is unavailable and failures can be silent;
  use `col->>'key' IS NOT NULL`.
- **`market_trades` has exactly one usable index**, `(exchange,
  exchange_trade_id)`, plus `(symbol, trade_ts)`. A predicate on `trade_ts`
  alone walks the whole index; a delete on `exchange_trade_id` alone walks the
  table. Both have caused multi-minute stalls.
- **pgvector lives in the `ag_catalog` schema** (it shares a database with AGE);
  new test databases must replicate that.
- **Migration head is 0038** (US market) on both tiers as of 2026-09-14. The
  `0038_us_market.py` file was applied while still uncommitted by another
  author — it is on disk and in both databases but not in git history.

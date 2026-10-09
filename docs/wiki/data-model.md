---
title: Data model
updated: 2026-10-09
sources: ["\\d on both tiers", infra/db/alembic/versions/]
status: current
---

Which table lives where, and the traps in that split.

## Claims
- **LOCAL (`matrix`)** — `market_trades` (18.3 M rows, 5.2 GB heap but 54 GiB
  of indexes on 2026-10-09; it was 284 M rows / ~90 GB on 09-19; 7-day
  retention, [[operations]] "Storage"), `margin_borrow_rates` (0041, public
  borrow quotes every 10 min), `market_ticker_snapshots`, `market_orderbook_snapshots`,
  `market_bars` (LIST-partitioned by asset class: `_crypto`, `_bist`, `_us`,
  `_other`), `raw_documents`, `bist_symbols`, `us_symbols`, `screener_*`, the
  AGE graph `matrix_graph`, and all `dev_*` tables.
- **SHARED (`matrix_shared`)** — `predictions`, `outcomes`, `paper_positions`,
  `wallets`, `wallet_snapshots`, `strategy_configs`, `strategy_slot_configs`,
  `mutation_proposals`, `agent_lessons`, `paper_trade_certificate`,
  `lab_experiments`, `lab_evaluations`, `tradable_symbols` (asset_class
  `crypto_carry` = the carry watchlist since 2026-10-09), `graph_signals`.
- **Ticker and book tables hold several venues under one symbol** since
  2026-10-09: `bybit` perp, `binance` (the funding poller), `bybit-spot` and
  `binance-spot` (carry spot legs). A reader that wants the traded perp must
  filter `exchange='bybit'`. Several did not, and were fixed in 688894b,
  b293edf and 6530b87. Spot 1m bars go to `market_bars` asset_class
  `crypto_spot`.
- **Tables that exist on both tiers are a hazard.** `dev_tasks`,
  `strategy_configs` and `wallets` were created on both by migrations; the
  owning service writes one copy and a probe reading the other gets an empty,
  error-free answer. This silently disabled dev_agent alerting and stranded a
  Director-filed task (2026-09-13). Rule: use the tier of the owning service.
- **`predictions.context` and `mutation_proposals.metrics_window` are `json`,
  not `jsonb`** — the `?` operator is unavailable and failures can be silent;
  use `col->>'key' IS NOT NULL`.
- **`market_trades` indexes**: `(exchange, exchange_trade_id)`,
  `(symbol, trade_ts)`, the pkey, and since 0039 `(trade_ts)` alone. The
  time index fixed retention but changed other plans ([[open-questions]]). A
  delete on `exchange_trade_id` alone still walks the table.
- **pgvector lives in the `ag_catalog` schema** (it shares a database with AGE);
  new test databases must replicate that.
- **Migration head is 0041** (`margin_borrow_rates`) as of 2026-10-09.
  `0038_us_market.py`, applied while still uncommitted, reached git in
  225d6ac.

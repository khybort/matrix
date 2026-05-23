# Roadmap (trading-first revision)

> Time-boxed milestones with explicit validation gates. Slipping a gate triggers a reassessment, not just "push the date right."
> No live capital is risked before Phase 5. No real money pressure until then — focus on building a system whose paper-trade performance is real enough to trust.

## Phase 0 — Foundation (Week 0, current)

- [x] Vision + architecture + work-split + trading docs
- [x] Monorepo skeleton
- [x] CLAUDE.md (node-agnostic)
- [x] `docker-compose.yml` — local Postgres + pgvector + AGE
- [ ] `.env.local` filled with at minimum: testnet broker keys + AI Gateway key + DB URL pointing at docker compose
- [ ] `infra/db` Alembic init + first migration (core tables)
- [ ] One smoke-test Python service that connects to DB and writes a row

**Gate:** `docker compose up -d && uv run python -m smoketest` works on at least one node.

---

## Phase 1 — First connector + first ingestion (Weeks 1-2)

**Chosen first market:** **Crypto perpetuals** via Bybit testnet (or Binance testnet — pick one and commit). Reasons in `docs/TRADING.md`.

- [ ] `services/ingestion`: Bybit WebSocket connector — pricing, order book snapshots, trades feed
- [ ] `services/ingestion`: crypto news feed (free RSS aggregate)
- [ ] Persist raw_documents + price snapshots
- [ ] Basic deduplication
- [ ] Run continuously for 1 week — verify uptime

**Gate:** 7 days of continuous ingestion with <5% gap time.

---

## Phase 2 — Graph extraction + retrieval (Weeks 3-4)

- [ ] `services/graph`: entity extraction (LLM with Haiku 4.5 — cost-discipline)
- [ ] Apache AGE schema deployed; nodes/edges upserted
- [ ] Embedding pipeline (pgvector, HNSW index)
- [ ] Hybrid retrieval API: graph traversal + vector + tsvector full-text
- [ ] Initial entity types: Asset, Event, Document, Concept

**Gate:** ad-hoc query "show me events likely impacting BTC over the past 24h" returns coherent results.

---

## Phase 3 — First strategy + paper-trade engine (Weeks 5-7)

- [ ] `services/strategy`: first strategy module — e.g. "news-driven short-horizon mean-reversion on majors"
- [ ] `services/backtest`/`paper-trade`: simulated execution with realistic slippage + fees
- [ ] Predictions table populated; outcomes scored after horizon
- [ ] Position sizing capped by `docs/TRADING.md` rules
- [ ] Run continuously paper-trading for 14 days

**Gate:** 14 days of paper-trade data with auditable PnL.

---

## Phase 4 — Self-improvement loop (Weeks 8-10)

- [ ] Weekly reflection agent: scores aggregated strategies + prompts → proposes mutations
- [ ] Strategy promotion/demotion mechanism (versioned, with safety guards)
- [ ] Add 2 more strategy hypotheses (e.g. funding-rate mean reversion, on-chain whale flows)
- [ ] Strategy-mutation audit log (every change reversible)

**Gate:** at least one strategy shows demonstrable improvement after 2 reflection cycles.

---

## Phase 5 — Small live capital (Weeks 11-14)

> Gate criteria to even attempt this: 60+ days of paper-trade data, positive expected value on at least one strategy, drawdown < 15% throughout.

- [ ] Switch one best paper-trade strategy from testnet to live exchange
- [ ] Cap risk capital at the user-defined "lose-it-all-OK" amount (suggest: $500-2000)
- [ ] Circuit breakers: hard stop on -20% drawdown, kill switch on any unusual order activity
- [ ] Daily P&L review + automated alerts on anomalies

**Gate:** 30 days of live operation without circuit breaker trip.

---

## Phase 6 — Multi-strategy live + multi-market (Months 4-6)

- [ ] Add second live strategy if first survives
- [ ] Add second market connector (US equities via Polygon, or BIST if available)
- [ ] Begin building the public-facing bulletin product on top of the now-running engine
- [ ] Start audience-building (Substack/X) using sanitized engine output

**Gate:** Survives 60+ days live with positive risk-adjusted return + bulletin has 100+ free subscribers.

---

## Phase 7+ — Asset state (Months 6-24)

- Scale capital cautiously on validated strategies
- Launch paid bulletin tier (deferred from original plan)
- Multi-market depth: FX, BIST, equities
- API tier for power users
- Asset-preparation: clean books, audit logs, exportable graph, valuation prep

---

## Catastrophic-risk firewall (applies to all phases)

The biggest danger is a software bug that drains capital faster than humans can react. Mandatory before any live capital:

1. **Hard position size caps** — strategy cannot exceed N% of capital per trade, enforced at the execution layer, not just the strategy
2. **Daily loss circuit breaker** — kills all open orders if daily P&L < -X%
3. **Per-market kill switch** — manual + automated triggers
4. **No new strategy goes live until 60+ days paper-trade history**
5. **Live capital starts at "amount you can lose entirely without lifestyle impact"** and only scales after explicit user decision

These are non-negotiable. See `docs/TRADING.md`.

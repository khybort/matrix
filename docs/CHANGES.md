# Cross-cutting Changes Log

> Append-only. Each entry: date + machine + one-line description of a cross-cutting change.
> Read this on every pull. If you're about to make a change that affects both Python and TS sides, add an entry here BEFORE coding.

---

## 2026-05-23 — Bootstrap (initial author: assistant, on behalf of user)

- Foundation docs created: `VISION.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `WORK_SPLIT.md`
- `CLAUDE.md` written with two-machine instructions
- Monorepo skeleton scaffolded (pnpm workspace)
- Tech stack defaults locked in `docs/ARCHITECTURE.md` (Next.js 16 + Python 3.13 + Neon + pgvector + AGE)

## 2026-05-23 — Pivot: trading-first, local-first, node-based

User clarified priorities after seeing the initial foundation. Three changes:

1. **Local-first**: removed Neon dependency. Local Postgres via `docker compose up -d`. Cloud (Neon + Vercel) only when bulletin product launches in Phase 6+. Added `docker-compose.yml` + `infra/db/Dockerfile` (apache/age + pgvector layered).
2. **Node-based**: dropped the fixed "Machine A / Machine B" model. Now `N` nodes can be added; each declares roles in `.matrix-node.json`. See revised `docs/WORK_SPLIT.md`.
3. **Trading-first**: bulletin product deferred to Phase 6+. Primary product is the autonomous self-improving trading system. New `docs/TRADING.md` with code-enforced risk framework. Roadmap rewritten around Phase 1-5 = build-then-paper-trade-then-small-live.

First market choice: **crypto perpetuals** (Bybit testnet) — 24/7 markets, low-friction testnet, smaller minimum capital, simpler regulatory positioning than equity options.

Next action: Phase 1 begins. First concrete coding task = bring up local DB + smoke-test Python service that writes a row.

## 2026-05-26 — Phase 5 gate: paper_trade_certificate table + has_valid_certificate()

Until this commit, `docs/TRADING.md` and the in-memory project rules promised a "code-enforced certificate gate" between paper trading and live capital, but no such table or check existed. Filled the gap before any live execution code is written, so the gate is in place when `services/execution/` lands.

- Migration `0011_paper_trade_certificate.py` (applies to LOCAL + SHARED tiers; runs via `make migrate` + `make migrate-local-shared`).
- `matrix_shared.models.PaperTradeCertificate` — one row per (strategy_id, asset_class, version). Status: pending|granted|revoked|expired. Carries evidence snapshot (n_outcomes, win_rate, total_pnl_usd, max_drawdown_pct, sharpe_ratio) and lifecycle fields (granted_at, granted_by, validity_until).
- `matrix_shared.trading_safety` — `has_valid_certificate(strategy_id, asset_class, version) -> bool` is the gate; `LIVE_EXECUTION_REQUIRES_CERT = True` is the hardcoded constant. `evaluate_eligibility(...)` reads outcomes + computes certificate-grant evidence. Drawdown anchored to a $10k reference (avoids the "tiny peak → huge percentage" trap when running PnL hovers near zero).
- Also made migration 0010 idempotent (`ADD COLUMN IF NOT EXISTS`), since the dev_agent test conftest seeded `dev_tasks.review_mode` directly into LOCAL — `make migrate` would otherwise fail with DuplicateColumn on first run.

Smoke-verified on live data: gate denies missing cert; eligibility for matrix_agent v1 returns `eligible=False` with reasons `[observation_days=1<60, win_rate=0.099<0.40, total_pnl=-17.32<0]` — exactly what we want pre-Phase-5.

## 2026-05-26 — matrix_agent diagnosis: signal direction works at long horizon, slippage kills short horizon

First empirical study of why the LLM-blended agent has been losing money. Used the historical replayer (Phase 3.5) to run three sweeps on 7d of BTCUSDT:

**Horizon sweep** (default weights, threshold 0.18). Win rate climbs monotonically with horizon:
- 60s → 4.76%
- 120s → 8.57%
- 300s → 22.86%
- 600s → 28.57%
- 1800s → 48.57%

Direction is roughly right at long horizons (approaching 50/50 = chance). At short horizons, the 0.04% round-trip slippage swamps everything.

**Threshold sweep** (horizon 120s). Tighter threshold → WORSE win rate (15.5% → 0% as threshold goes 0.10 → 0.35). "High conviction" trades aren't more predictive; they're extrema.

**Signal attribution** (each signal solo, threshold 0.01). At h=120s nothing carries alpha: trade_flow 13%, funding 16%, oi_delta 17%, ob_imbalance 16%, news 18%. At h=1800s, only oi_delta (42% win, -$6.86) and news (38% win, -$26.94) are even close to break-even.

Verdict: at current horizons the blend is averaging noise. Total PnL stays negative across horizon settings because losers are systematically bigger than winners — direction salvageable, magnitude not (yet). Next step is TP/SL in the replayer + a config that's oi_delta + news heavy at long horizons. Deployed `matrix_agent v3` (40% oi_delta, 40% news, threshold 0.15, horizon 1800s) as the hypothesis test — needs 7+ days of live paper data to evaluate.

CLI: `make diagnose-matrix-agent SYMBOL=BTCUSDT DAYS=7` reproduces this on demand. Operator should re-run weekly and compare deltas.

## 2026-05-26 — Market parity: pluggable MarketAdapter (Phase A-G)

BIST and crypto pipelines were "half-symmetric" — DB had asset_class, ingestion was two services, strategies mixed flat-crypto + bist-subfolder, agent_lessons + wallet had no per-market split. This commit chain reshapes the whole pipeline around a single abstraction so adding a new market = one adapter + a registry entry, not a refactor of every service.

**Core:** `packages/python-shared/src/matrix_shared/markets/` — `MarketAdapter` ABC + `FeeModel` + `ExecutionAdapter` / `IngestorAdapter` protocols; `register/get_market/all_markets/infer_market` registry; concrete `CryptoMarket` + `BistMarket`. `infer_market(symbol)` routes by claims_symbol; BIST cache primes on universe refresh, falls back to regex pre-warm.

**Pipeline phases:**
- B (strategy): `modules/crypto/{dca,grid,funding_reversion,oi_breakout,oi_delta}.py` mirrors `modules/bist/`. main.py dispatcher walks all_markets() × STRATEGIES_BY_MARKET. `trade_flow_imbalance` removed (retired 2026-05-26).
- C (wallet): migration 0017 adds `wallets.asset_class` (default `crypto`), drops single-column `name` unique, adds composite `(name, asset_class)`. risk_caps.yaml provides per-market seed defaults (BIST gets fewer slots + wider DD).
- D (execution): per-market `ExecutionAdapter` factories. `CryptoLiveExecutor` wraps the existing BybitConnector; `BistLiveExecutor` stub raises NotImplementedError on every action (Phase 1 wall). Daemon heartbeat walks all_markets() so the not-wired markets show up next to wired ones.
- E (agent_lessons): migration 0018 adds `agent_lessons.asset_class`. synthesize + feed_once take an asset_class kwarg; per-market active-lesson index + filter. agent_lessons daemon runs one synth+feed pass per market each cycle. agent main.py uses MarketAdapter.is_session_open + universe — no more hard-coded BIST helpers.
- F (ingestion): `services/bist-ingestion` folded into `services/ingestion`. ingestion.main is a market fan-out (asyncio task per registered adapter). Compose `bist-ingestion` service deleted; one image, one log stream.
- G (web): `/api/markets` route mirrors the registry with a live stats join (universe, predictions, positions, wallet, realised PnL). `/api/dashboard?market=` filters recent predictions + outcomes. Dashboard gains a "Registered market adapters" panel.

Out of scope (Phase 1+): BIST live broker integration (AlgoLab / Garanti API); MarketAdapter ABC for paper-trade engine (still in services/backtest, not behind ExecutionAdapter); cross-market lesson transfer; dynamic capital allocation across markets.

## 2026-05-27/28 — Agent-driven architecture + Brain chat (branch `feat/agent-driven-brain`)

Operator brief: re-architect the system to be agent-driven, keep context in the knowledge graph, expose a super-intelligent "ask anything" chat across web + Telegram + API. Strangler-fig migration; the paper-trade loop and the live-execution certificate gate stay code-enforced throughout.

**Framework override** (vs. `ARCHITECTURE.md:110` naming LangGraph): the runtime is a thin layer built ON `claude_agent_sdk`'s native tool loop (proven in `services/dev_agent/src/dev_agent/sdk_runner.py`) — multi-step tool loops on subscription auth via in-process MCP servers (`create_sdk_mcp_server` + `@tool`), `can_use_tool` deny-hook, `max_turns`. LangGraph's tool loop expects an API-key chat client; that's exactly the path this repo blanks out (`docker-compose.yml` clears `ANTHROPIC_API_KEY`). Reuse the SDK loop; don't pay for LangGraph to get a state machine you can express in ~200 lines.

**Shared agent runtime** (`packages/python-shared/src/matrix_shared/agent_runtime/`): `runtime.py` `run_agent_stream` (normalized AgentEvents, bounded by `max_turns` + a `can_use_tool` deny-hook + one shared rate-limiter slot); `tool.py` `Tool`/`ToolRegistry`/`@tool` with side-effect classes (`read`/`write`/`risk-gated`); `guards.py` read-only SQL (SELECT/WITH only, table allow-list, auto-LIMIT) + Cypher (rejects CREATE/MERGE/SET/DELETE/...) gates; `ratelimit.py` process-shared semaphore. `subscription_llm.call_subscription_agent` is the tool-loop entry on the subscription path (keeps single-shot `call_subscription` intact).

**Matrix Brain** (`services/brain`, port 3032): Opus-4.7 read-only "ask anything" agent. FastAPI SSE `/chat`, conversation persistence (`chat_sessions` / `chat_messages`, migration 0020). Tool belt: `list_tables`, `describe_table`, `sql_read` (allow-listed, tier-routed crypto/graph LOCAL vs domain SHARED), `cypher_query` (read-only AGE). Hard guardrails: no write tool registered; `disallowed_tools=[Bash,Write,Edit,NotebookEdit]`; `can_use_tool` deny-hook; `assert_all_read_only` at startup. The Brain cannot place trades, move money, or grant certificates.

**Chat surfaces**: web `/chat` page + thin `/api/chat` SSE pass-through (no AI SDK in JS — the model lives in Python on subscription); Telegram free-text via `services/notify` extension routes to the same brain SSE (session `external_ref` = chat id).

**Context-graph overlay** (`services/graph/overlay.py`): reasoning episodes — `Prediction`/`Decision`/`Outcome`/`Lesson`/`Strategy` — layered on the AGE graph as an INDEX over the ACID relational rows (node keys = relational PKs; money/risk stay authoritative in Postgres). Best-effort idempotent MERGE; a failed graph write never breaks the relational write. The agent dual-writes a Prediction node (+ `PRODUCED` from Strategy, `PREDICTS` Asset via BTCUSDT→BTC canonical) when it persists. `graph_summary` counts overlay labels so the dashboard sees them.

**Exploration (more positions → faster learning)**: epsilon-greedy at `decide.maybe_explore` — with `AgentConfig.explore_epsilon` (default 0.15) flips a `hold` into a low-confidence trade in the sub-threshold lean, tagged `is_exploration` (lands in `prediction.context`). Runs BEFORE the lessons gate so `avoid` lessons still veto; respects BIST long-only. Concurrency cap raised: `wallets.max_concurrent_positions` 5 → 15 (migration 0021 lifts existing wallets still at the old default). Fixed an interaction bug: `agent.main` `confidence<0.1` floor was dropping exploration trades — now bypassed when `is_exploration`.

**BIST / crypto data separation**: `market_bars` is LIST-partitioned on `asset_class` (migration 0022); crypto and BIST rows live in physically separate partitions (`market_bars_crypto` / `market_bars_bist` / `_other` DEFAULT). PK becomes `(id, asset_class)` and the `(symbol, interval, ts)` uniqueness becomes `(asset_class, symbol, interval, ts)` named `uq_market_bars_class_sit` (partitioning requires the partition key in every unique/PK constraint). All existing ORM queries unchanged; ingestion upserts retargeted. `market_trades` is already crypto-only (BIST has no tick data) so unchanged.

**First strangler-fig conversion**: `services/synthesis` drives the SDK tool loop with read-only tools (`recent_documents`, `existing_concepts`, `existing_assets`) so themes are grounded in existing graph coverage and don't duplicate Concepts. Legacy single-shot path kept as fallback. Concept upserts stay in service code — the LLM never touches graph state.

Remaining conversions (sequencing — each behind the stable `predictions` interface, with `rule_decide` fallback preserved on the hot path): graph extraction → reflection + agent_lessons → labs → decision agent (last) → execution (only when broker connector lands; `submit_order` will be `risk-gated`, never converted to an agent decision).

Risks tracked: subscription rate/latency × many multi-step agents (mitigated by the shared `agent_runtime.ratelimit` semaphore and deterministic fallbacks on the hot path); graph/SQL divergence (SQL is source of truth, graph writes idempotent + best-effort, never read money/cert from graph); brain data exposure (read-only belt + SQL/Cypher gates + `disallowed_tools` + deny-hook, defense in depth).

## 2026-05-28 — Token optimization + birincil amaç netleştirildi

- Operatör gözlemi: Brain her sorguda Opus 4.7 koşturuyordu (~$0.20/turn); rate bütçesi
  decision loop ile paylaşılıyor. 6 commit'lik token-disiplini turu:
  - `agent.usage` telemetrisi (`subscription_llm` tek nokta, brain SSE result.model). Önce ölç.
  - Brain default `claude-sonnet-4-6`, `BRAIN_MODEL=opus` veya per-istek `{"model":"…"}` opt-in.
    Aynı sorguda **Opus $0.197 → Sonnet $0.022, ~9× ucuz**.
  - Tool çıktıları: brain `sql_read`/`cypher_query` default head/tail+summary (`_truncate_row`
    + `_summarize_rows`), `verbose=true` ile eski davranış. Synthesis/graph/reflection backend
    agent'larında MAX_DOCS, BODY_CHARS, recent_outcomes LIMIT'leri sıkıştırıldı (verbose opt-in).
  - System prompt'lar modül-seviye literal'a alındı (reflection/graph extract): CLI içi prompt
    cache'e tek seviyemizden uygun zemin. `claude_agent_sdk`'da explicit `cache_control` yok.
  - `make agent-usage` operatör görünürlüğü.
- `CLAUDE.md`: "Birincil amaç — her zaman yüksek profit" bölümü en üste eklendi; her teknik
  kararın aktif süzgeci olarak. Token tasarrufu kendi başına amaç değil — rate-budget'ın
  decision loop'a kalması içindir.

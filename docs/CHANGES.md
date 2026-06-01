# Cross-cutting Changes Log

> Append-only. Each entry: date + machine + one-line description of a cross-cutting change.
> Read this on every pull. If you're about to make a change that affects both Python and TS sides, add an entry here BEFORE coding.

---

## 2026-06-02 — Public dashboard overlay (DuckDNS / host :3030)

- `docker-compose.public.yml` + `make up-dev-public`, `make public-ip`, `scripts/duckdns-update.sh`

## 2026-06-02 — Exchange shadow: paper trade mirrors to Bybit testnet

- `matrix_shared/exchange_shadow.py`: when `LIVE_EXECUTION_ENABLED=true`, paper
  opens/closes also submit market orders to Bybit (testnet via `BYBIT_TESTNET`).
  Paper DB stays source of truth; exchange errors are logged only.
- Env: `MATRIX_EXCHANGE_SHADOW=true` (default). Cert + per-trade size gate apply;
  total `LIVE_CAPITAL_CAP_USD` does not block shadow (paper locked can exceed cap).

## 2026-06-02 — BIST 1m bar backfill (strategy preconditions)

- `ingestion/bist/bars.py`: bootstrap + off-session 6h refresh for `1m` bars when missing/stale
- `make bist-bars-backfill`: one-shot `1m` / `5d` pull via yfinance (works outside TR session)

## 2026-06-01 — Cursor Auto LLM backend (`make llm-cursor`)

Third LLM backend alongside subscription and Bedrock. `make llm-cursor` sets
`MATRIX_LLM_BACKEND=cursor`; all LLM calls use **model=auto** (tier pins ignored).
Auth is subscription-style: `cursor agent login` on the host (browser OAuth);
`CURSOR_API_KEY` is optional for CI/Docker. Single-shot calls use `cursor agent -p`;
tool loops use `cursor-sdk` with the same login or API key. Docker: Cursor CLI
installed in graph/agent/brain/synthesis/reflection images; dev compose uses a
`matrix_cursor_agent` volume on `/root/.cursor` (`make cursor-login-docker` or
`docker exec -it matrix-agent cursor agent login` once per machine — do not mount macOS
`~/.local/share/cursor-agent`, it breaks the Linux CLI). Restart stack after
switching. **dev_agent** still uses `claude_agent_sdk` directly (not wired).

---

## 2026-06-01 — Dev CPU limits (docker-compose.limits.yml)

`make up-dev` / `up-dev-local` merge `docker-compose.limits.yml`. Default
**performance** caps: heavy 1.0, db 0.75, worker 0.5, light 0.25. Tighten via
`MATRIX_CPU_*` in `.env`. `make up-dev-core` = trading core only (7 services).
Recreate after limit changes: `--force-recreate`.

---

## 2026-06-01 — Dev watchfiles debounce (restart storm)

`docker-compose.dev.yml` uses `matrix_shared.dev_watchfiles` instead of the
stock `watchfiles` CLI: 2.5s debounce, 2s post-start grace, ignores
`tests/` + tool caches. Tunable via `MATRIX_WATCH_DEBOUNCE_MS` /
`MATRIX_WATCH_GRACE_S`. Recreate dev containers after pull:
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`.

---

## 2026-06-01 — Profit loop fix: align mutations + lab scoring with paper PnL

Self-improvement was optimizing the wrong objective and mutating in the wrong direction:

- **reflection/mutate**: rule path now triggers on negative `total_pnl_usd`, shifts weight toward `oi_delta`/`news`, *lowers* threshold, extends `horizon_seconds`, reduces `explore_epsilon` (reverses the pre-2026-06 heuristic that dampened news and raised threshold). `matrix_agent` added to `PARAM_TUNERS` + safe auto-apply.
- **reflection/metrics**: `win_rate` now counts `pnl_usd > 0`, not `score > 0`.
- **labs/evaluate**: slippage-adjusted PnL via `matrix_shared.trading.apply_slippage`; wins counted on `pnl_pct > 0`.
- **labs/promote**: merge patches into existing params (no more partial overwrites); lab promotions enriched with `tp_pct`/`sl_pct`/`explore_epsilon`; `_params_equivalent` includes `horizon_seconds`.
- **agent**: v3 defaults (1800s horizon, 40/40 oi_delta/news, threshold 0.15, explore 5%); OI score aligned with 5m price direction.
- **migration 0034**: upgrades legacy short-horizon `matrix_agent` configs to v3 params.

Run `make migrate` to apply 0034 on existing DBs.

---

## 2026-06-01 — Seed missing strategy_configs (0035)

Registered crypto/BIST modules were skipped by the dispatcher because no
`strategy_configs` row existed (0026/0027 only UPDATE). Migration 0035
INSERTs grid, funding_reversion, oi_delta + four BIST strategies with slot
configs. Also fixes `uq_strategy_configs_id_ver` → per-asset_class unique
and seeds BIST `matrix_agent`. Run `make migrate-local-shared`.

---

## 2026-06-01 — Re-enable dca + oi_breakout for analysis (0036)

Registry + migration re-open `dca` and `oi_breakout` with slot configs.
Crypto wallet cap raised 50→80 for parallel paper analysis. `bars.py` now
rolls 1m→1h bars so `momentum_xs` can fire. Run migrate + restart
`bars-aggregator` and `strategy`.

---

## 2026-06-01 — matrix_agent slot cap + dedup + bar rollup tuning (0037)

- Migration 0037: `matrix_agent` capped at 18 concurrent slots; other crypto
  strategies rebalanced (sum of caps 64 / wallet 80).
- `agent/main.py`: horizon dedup — no repeat signal per symbol within 1800s.
- `explore_epsilon` lowered to 0.03.
- `bars-aggregator`: lookback 120m + 168h REST backfill on startup (compose).

---

## 2026-06-01 — Profit-first allocation: strategy×symbol fit + dynamic risk

- `matrix_shared/allocation.py`: pair-edge from closed paper trades, EV ranking
  (symbol + strategy perf + pairing), and `risk_multiplier` for notional sizing.
- `paper_trade.py`: candidates sorted by full EV; position size scales with
  confidence, strategy `perf_score`, pair history, and consecutive losses.

---

## 2026-06-01 — Dynamic universe: remove hardcoded crypto fallback

- `crypto_universe()`: no `_DEFAULT_UNIVERSE`; reads `tradable_symbols.active`,
  cold-bootstraps from `screener_universe_snapshot`, else empty.
- Compose: `UNIVERSE_MANAGER_ENABLED/ENFORCE=true` by default; ingestion no
  longer passes BTCUSDT/ETHUSDT on the CLI.

---

## 2026-06-01 — Dev watchfiles crash-loop fix

- `Dockerfile`: `uv pip install watchfiles` after service `uv sync` so dev
  hot-reload works in every service venv.
- `docker-compose.dev.yml`: entrypoint uses `uv run --with watchfiles` +
  `matrix_shared.dev_watchfiles` (debounced reload).

---

## 2026-06-01 — BIST dynamic discover (no embedded seed)

- `ingestion/bist/discover.py`: fetches ~600+ tickers from configurable JSON
  endpoint (default Fintables public API); upserts `bist_symbols`, deactivates
  delisted. Embedded `BIST_SEED` list removed.
- BIST ingestor runs discover on startup + daily refresh (`BIST_DISCOVER_INTERVAL_S`).
- `make bist-seed` → `matrix-bist-symbols --bootstrap-active` via ingestion-market.

---

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

## 2026-05-28 (later) — Worker pattern (Haiku distillation) — proven live

3 commit'lik mini-tur: orchestrator/worker model tieringi gerçek koda taşındı.

- `matrix_shared.agent_runtime.worker.haiku_distill`: tek-atış Haiku 4.5 çağrısı (subscription path), opsiyonel/ücretsiz; subscription kapalıysa deterministic truncate fallback.
- İlk uygulama: `synthesis.recent_documents` tool'u artık DAİMA Haiku-distilled 5-10 bullet özeti döner. `verbose=true` opt-out kaldırıldı (operatör memory'sindeki ders: model verbose'a kaçıyor — distillation tool seviyesinde *yapısal* enforce edilmeli).
- `call_subscription` single-shot path da `agent.usage` log atıyor (Haiku worker + legacy fallback'ler artık `make agent-usage`'da görünür).

Canlı ölçüm (aynı synthesis tick'i, öncesi/sonrası): Sonnet orkestratör cost **$0.183 → $0.086 (−53%)**, Haiku worker $0.032 eklenince **total $0.118 (−35%)**. Tema sayısı 7 → 4 (recall düştü, precision yüksek; özetin verdiği focus daha az gürültü demek).

Sonraki uygulama alanları (canlı veri toplandıkça): brain `sql_read` (büyük tablolar), reflection `recent_outcomes` (uzun PnL izleri), brain `cypher_query` (uzun sonuç listeleri).

## 2026-05-28 (later still) — Provider-portable LLM (subscription / Bedrock / Vertex)

`claude_agent_sdk` zaten backend seçimini standart env'lerle yapıyor; iki düzeltme yetti:

1. **Hardcoded model adları indirection'a alındı.** `matrix_shared.subscription_llm`'de
   `MODEL_HAIKU / MODEL_SONNET / MODEL_OPUS` modül-seviye sabitleri, `MATRIX_MODEL_<role>`
   env'inden çözülür (yoksa Anthropic kısa adları — eski davranış). 10 call site bu sabitleri
   kullanır.
2. **Compose env passthrough.** `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`,
   `AWS_REGION/AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN/AWS_PROFILE`,
   `MATRIX_MODEL_<HAIKU|SONNET|OPUS>` artık `*python-env` anchor'ında.

`subscription_enabled()` tek bir provider'dan biri yapılandırıldıysa True döner (subscription /
Bedrock / Vertex) — isim geriye dönük uyum için korundu, anlamı "LLM yolu hazır mı?".

**Subscription operatörü için**: hiçbir değişiklik gerekmiyor.

**Bedrock'a geçmek için** `.env`'e şunları yaz, `make build && docker compose up -d` ile yenile:

```
CLAUDE_CODE_USE_BEDROCK=1
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
MATRIX_MODEL_HAIKU=us.anthropic.claude-haiku-4-5-20251001-v1:0
MATRIX_MODEL_SONNET=us.anthropic.claude-sonnet-4-6-20250929-v1:0
MATRIX_MODEL_OPUS=us.anthropic.claude-opus-4-7-20251022-v1:0
```

Brain + 5 backend agent (synthesis / graph extract / reflection / decision LLM / bulletin) + Haiku
worker birlikte flip eder. `CLAUDE_CODE_OAUTH_TOKEN` set'liyse Bedrock yine de override eder
(CLI'nın iç önceliği bu yönde).

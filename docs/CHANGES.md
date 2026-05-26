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

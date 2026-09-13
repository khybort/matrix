# Matrix — common operations
#
# Default target prints help. Use `make <target>`.
# Profiles:
#   dev   → hot-reload, source bind-mounts; for active development
#   prod  → built images, restart-always; for long-running deploys
#
# Quick start (dev):
#   make build         # build all images
#   make migrate       # run alembic migrations
#   make up-dev        # start everything in dev mode
#   make logs          # follow all logs
#   make dashboard     # open http://matrix.local

DC          := docker compose
DC_BASE     := -f docker-compose.yml
DC_LIMITS   := -f docker-compose.limits.yml
DC_DEV      := $(DC_BASE) -f docker-compose.dev.yml $(DC_LIMITS)
DC_PROD     := $(DC_BASE) -f docker-compose.prod.yml
DC_LOCAL    := $(DC_BASE) -f docker-compose.dev.yml -f docker-compose.local.yml $(DC_LIMITS)
DC_PUBLIC   := -f docker-compose.public.yml

PSQL        := $(DC) exec postgres psql -U matrix -d matrix
PSQL_SHARED := $(DC) exec postgres-shared psql -U matrix -d matrix_shared

.DEFAULT_GOAL := help

# ----------------------------------------------------------------- meta

.PHONY: help
help:
	@awk 'BEGIN {FS = ":.*##"; printf "\nMatrix targets:\n"} \
		/^[a-zA-Z_.-]+:.*?##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 } \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@echo ""

##@ Lifecycle

.PHONY: build
build: ## Build all images for the base topology (prod target)
	$(DC) $(DC_BASE) build

.PHONY: build-dev
build-dev: ## Build images with dev targets (web → 'dev' stage with HMR)
	$(DC) $(DC_DEV) build

.PHONY: disk
disk: ## Show docker disk usage (run this BEFORE long build iterations)
	@docker system df
	@echo ""
	@echo "If 'RECLAIMABLE' is >30GB, run: make disk-clean"

.PHONY: disk-clean
disk-clean: ## Reclaim disk: build cache + dangling images (volumes are safe)
	docker builder prune -f
	docker image prune -f
	@echo ""
	@docker system df

.PHONY: disk-watch
disk-watch: ## Tail docker disk usage every 30s (Ctrl-C to stop)
	@while true; do clear; docker system df; echo ""; date; sleep 30; done

.PHONY: up
up: ## Start the base stack (no overrides)
	$(DC) $(DC_BASE) up -d

.PHONY: up-dev
up-dev: ## Start everything in dev mode (hot reload, SHARED → .env URL)
	$(DC) $(DC_DEV) up -d

.PHONY: up-dev-local
up-dev-local: ## Dev mode + local postgres-shared (no Neon needed; full offline)
	$(DC) $(DC_LOCAL) up -d

.PHONY: up-dev-public
up-dev-public: ## Dev stack + web on 0.0.0.0:3030 (internet / DuckDNS / sslip.io)
	$(DC) $(DC_DEV) $(DC_PUBLIC) up -d

.PHONY: up-prod-public
up-prod-public: ## Prod stack + web on 0.0.0.0:3030
	$(DC) $(DC_PROD) $(DC_PUBLIC) up -d

.PHONY: public-ip
public-ip: ## Print public IPv4 + sslip.io hostname for :3030
	@IP=$$(curl -4 -fsS --max-time 10 https://api.ipify.org); \
	H=$$(echo "$$IP" | tr '.' '-'); \
	echo "Public IP:  $$IP"; \
	echo "Dashboard:  http://$$IP:3030"; \
	echo "sslip.io:   http://$$H.sslip.io:3030  (no signup; updates when IP changes)"

.PHONY: duckdns-update
duckdns-update: ## Point DUCKDNS_SUBDOMAIN.duckdns.org at this host (needs .env vars)
	@./scripts/duckdns-update.sh

.PHONY: up-dev-core
up-dev-core: ## Dev core only: DB + ingestion + bars + graph + agent + strategy (CPU limits)
	$(DC) $(DC_LOCAL) up -d postgres postgres-shared ingestion-market bars-aggregator graph agent strategy

.PHONY: up-prod
up-prod: ## Start everything in prod mode (built images, restart=always)
	$(DC) $(DC_PROD) up -d

.PHONY: down
down: ## Stop and remove all containers
	$(DC) $(DC_BASE) down

.PHONY: nuke
nuke: ## Stop + remove volumes (destroys local DB data)
	$(DC) $(DC_BASE) down -v

.PHONY: restart
restart: down up-dev ## Down then up-dev

##@ DB

.PHONY: migrate
migrate: ## Run alembic upgrade head on LOCAL tier (always rebuilds migrate image)
	$(DC) $(DC_BASE) --profile migrate build migrate
	$(DC) $(DC_BASE) --profile migrate run --rm migrate upgrade head

.PHONY: migrate-local-shared
migrate-local-shared: ## Apply migrations to the local postgres-shared (fake Neon)
	$(DC) $(DC_LOCAL) --profile migrate build migrate
	$(DC) $(DC_LOCAL) --profile migrate run --rm \
		-e DATABASE_URL=postgres://matrix:matrix_dev_only@postgres-shared:5432/matrix_shared \
		-e LOCAL_DATABASE_URL=postgres://matrix:matrix_dev_only@postgres-shared:5432/matrix_shared \
		migrate upgrade head

.PHONY: migrate-shared
migrate-shared: ## Apply migrations to SHARED tier as set in .env (use this for Neon)
	$(DC) $(DC_BASE) --profile migrate run --rm \
		-e DATABASE_URL=$${SHARED_DATABASE_URL} \
		-e LOCAL_DATABASE_URL=$${SHARED_DATABASE_URL} \
		migrate upgrade head

.PHONY: migrate-down
migrate-down: ## Roll back the most recent migration (LOCAL)
	$(DC) $(DC_BASE) --profile migrate run --rm migrate downgrade -1

.PHONY: migrate-new
migrate-new: ## Create a new migration: make migrate-new MSG="add foo table"
	$(DC) $(DC_BASE) --profile migrate run --rm migrate revision --autogenerate -m "$(MSG)"

.PHONY: psql
psql: ## Open a psql shell on the LOCAL postgres
	$(PSQL)

.PHONY: psql-shared
psql-shared: ## Open a psql shell on the local SHARED postgres (port 5433)
	$(PSQL_SHARED)

.PHONY: db-reset
db-reset: ## Truncate dynamic tables (predictions, paper_positions, outcomes, snapshots, lab_*)
	$(PSQL) -c "TRUNCATE market_trades, market_orderbook_snapshots, market_ticker_snapshots, predictions, paper_positions, outcomes, wallet_snapshots, mutation_proposals CASCADE; DELETE FROM lab_evaluations; DELETE FROM lab_experiments; UPDATE wallets SET cash_usd = starting_capital_usd, locked_usd = 0, circuit_tripped_at = NULL;"

##@ Backup / restore

BACKUP_DIR := backups
BACKUP_TS  := $(shell date +%Y%m%d-%H%M%S)

.PHONY: backup
backup: ## pg_dumpall LOCAL + SHARED to ./backups/<ts>/
	@mkdir -p $(BACKUP_DIR)/$(BACKUP_TS)
	@echo "→ dumping LOCAL..."
	$(DC) exec -T postgres pg_dumpall -U matrix > $(BACKUP_DIR)/$(BACKUP_TS)/local.sql
	@echo "→ dumping SHARED-local (postgres-shared) if running..."
	-$(DC) exec -T postgres-shared pg_dumpall -U matrix > $(BACKUP_DIR)/$(BACKUP_TS)/shared.sql 2>/dev/null && echo "  ✓ shared.sql" || echo "  (skipped — postgres-shared not running)"
	@echo "→ backup at $(BACKUP_DIR)/$(BACKUP_TS)/"
	@ls -lh $(BACKUP_DIR)/$(BACKUP_TS)/

.PHONY: circuit-reset
circuit-reset: ## Operator reset of a tripped daily-loss circuit: make circuit-reset ASSET=crypto [WALLET=default]
	@if [ -z "$(ASSET)" ]; then echo "Usage: make circuit-reset ASSET=<asset_class> [WALLET=default]" && exit 1; fi
	$(PSQL_SHARED) -c "UPDATE wallets SET circuit_tripped_at = NULL, day_start_equity = cash_usd + locked_usd, day_start_at = now() WHERE asset_class='$(ASSET)' AND name='$(or $(WALLET),default)' AND circuit_tripped_at IS NOT NULL RETURNING name, asset_class;"

.PHONY: reset-capital
reset-capital: ## Reset a paper wallet's capital WITHOUT touching learning data: make reset-capital ASSET=crypto [WALLET=default] [AMOUNT=+346.76]
	@if [ -z "$(ASSET)" ]; then echo "Usage: make reset-capital ASSET=<asset_class> [WALLET=default] [AMOUNT=<signed usd, default: back to starting capital>]" && exit 1; fi
	@if [ -n "$(AMOUNT)" ]; then \
	  $(PSQL_SHARED) -c "UPDATE wallets SET cash_usd = cash_usd + ($(AMOUNT)), updated_at = now() WHERE asset_class='$(ASSET)' AND name='$(or $(WALLET),default)' RETURNING name, asset_class, round(cash_usd+locked_usd,2) AS equity;"; \
	else \
	  $(PSQL_SHARED) -c "UPDATE wallets SET cash_usd = starting_capital_usd - locked_usd, day_start_equity = starting_capital_usd, day_start_at = now(), circuit_tripped_at = NULL, updated_at = now() WHERE asset_class='$(ASSET)' AND name='$(or $(WALLET),default)' RETURNING name, asset_class, round(cash_usd+locked_usd,2) AS equity;"; \
	fi

.PHONY: backup-now
backup-now: ## Run one scheduled-style backup now (pg_dump -Fc, market streams schema-only) → ./backups/<ts>/
	$(DC) $(DC_BASE) run --rm backup once

.PHONY: retention-drain
retention-drain: ## Prune ALL rows past retention windows now (bars-aggregator does this gradually); MAX_MIN caps runtime
	$(DC) $(DC_BASE) exec bars-aggregator uv run python -c "import asyncio; from matrix_shared.retention import drain; print(asyncio.run(drain(max_minutes=float('$(or $(MAX_MIN),0)'))))"

.PHONY: db-compact
db-compact: ## VACUUM FULL one LOCAL table to return disk to the OS (LOCKS the table): make db-compact TABLE=market_trades
	@if [ -z "$(TABLE)" ]; then echo "Usage: make db-compact TABLE=<table>" && exit 1; fi
	$(PSQL) -c "VACUUM (FULL, VERBOSE, ANALYZE) $(TABLE);"

.PHONY: restore-local
restore-local: ## Restore LOCAL from a dump file: make restore-local FILE=backups/<ts>/local.sql
	@if [ -z "$(FILE)" ]; then echo "Usage: make restore-local FILE=<path>" && exit 1; fi
	@echo "→ restoring $(FILE) → LOCAL (this DROPS existing data)"
	$(DC) exec -T postgres psql -U matrix -d postgres -c "DROP DATABASE IF EXISTS matrix;"
	$(DC) exec -T postgres psql -U matrix -d postgres -c "CREATE DATABASE matrix;"
	cat $(FILE) | $(DC) exec -T postgres psql -U matrix -d matrix
	@echo "✓ LOCAL restored"

.PHONY: restore-shared
restore-shared: ## Restore SHARED-local: make restore-shared FILE=backups/<ts>/shared.sql
	@if [ -z "$(FILE)" ]; then echo "Usage: make restore-shared FILE=<path>" && exit 1; fi
	@echo "→ restoring $(FILE) → SHARED-local"
	$(DC) exec -T postgres-shared psql -U matrix -d postgres -c "DROP DATABASE IF EXISTS matrix_shared;"
	$(DC) exec -T postgres-shared psql -U matrix -d postgres -c "CREATE DATABASE matrix_shared;"
	cat $(FILE) | $(DC) exec -T postgres-shared psql -U matrix -d matrix_shared
	@echo "✓ SHARED-local restored"

.PHONY: backup-list
backup-list: ## List existing backups
	@ls -lhRt $(BACKUP_DIR) 2>/dev/null || echo "(no backups yet)"

##@ Inspection

.PHONY: ps
ps: ## Show container status
	$(DC) $(DC_BASE) ps

.PHONY: logs
logs: ## Follow logs from all services
	$(DC) $(DC_BASE) logs -f --tail=100

.PHONY: logs-agent
logs-agent: ## Follow only the agent
	$(DC) $(DC_BASE) logs -f --tail=200 agent

.PHONY: logs-labs
logs-labs: ## Follow only the labs service
	$(DC) $(DC_BASE) logs -f --tail=200 labs

.PHONY: logs-brain
logs-brain: ## Follow only the brain (chat) service
	$(DC) $(DC_BASE) logs -f --tail=200 brain

.PHONY: agent-usage
agent-usage: ## Recent agent.usage log lines across services (cost + turns)
	$(DC) $(DC_BASE) logs --no-log-prefix --tail=4000 \
	  brain synthesis graph reflection agent 2>&1 \
	  | grep -F 'agent.usage' | tail -200

.PHONY: dashboard
dashboard: ## Open the dashboard in your browser
	@open http://matrix.local 2>/dev/null || xdg-open http://matrix.local 2>/dev/null || \
		echo "Open http://matrix.local in your browser"

.PHONY: leaderboard
leaderboard: ## Print lab leaderboard (MARKET=crypto|bist|all; default all)
	$(DC) $(DC_BASE) exec labs uv run python -m labs.main --leaderboard $(if $(MARKET),--asset-class $(MARKET),)

.PHONY: market-stats
market-stats: ## Per-market counts (predictions, positions, lessons; MARKET=crypto|bist; default both)
	@$(PSQL) -c "SELECT asset_class, \
			COUNT(*) FILTER (WHERE status='open')   AS preds_open, \
			COUNT(*) FILTER (WHERE status='closed') AS preds_closed \
		FROM predictions \
		$(if $(MARKET),WHERE asset_class='$(MARKET)',) \
		GROUP BY asset_class ORDER BY asset_class;"
	@$(PSQL) -c "SELECT asset_class, \
			COUNT(*) FILTER (WHERE status='open')   AS pos_open, \
			COUNT(*) FILTER (WHERE status='closed') AS pos_closed, \
			COALESCE(SUM(pnl_usd) FILTER (WHERE status='closed'),0)::numeric(18,4) AS realized_pnl_usd \
		FROM paper_positions \
		$(if $(MARKET),WHERE asset_class='$(MARKET)',) \
		GROUP BY asset_class ORDER BY asset_class;"
	@$(PSQL) -c "SELECT asset_class, COUNT(*) AS active_lessons \
		FROM agent_lessons WHERE status='active' \
		$(if $(MARKET),AND asset_class='$(MARKET)',) \
		GROUP BY asset_class ORDER BY asset_class;"
	@$(PSQL) -c "SELECT asset_class, COUNT(*) AS wallets, \
			COALESCE(SUM(cash_usd),0)::numeric(18,4) AS cash_usd, \
			COALESCE(SUM(locked_usd),0)::numeric(18,4) AS locked_usd \
		FROM wallets \
		$(if $(MARKET),WHERE asset_class='$(MARKET)',) \
		GROUP BY asset_class ORDER BY asset_class;"

.PHONY: tp-sl-ratio
tp-sl-ratio: ## Per-strategy outcome reason breakdown (hit_tp / hit_sl / hit_horizon / other)
	@$(PSQL_SHARED) -c "WITH win_loss AS ( \
	  SELECT p.strategy_id, o.reason, \
	         COUNT(*) AS n, \
	         ROUND(SUM(o.pnl_usd)::numeric, 2) AS pnl \
	  FROM outcomes o JOIN predictions p ON p.id = o.prediction_id \
	  WHERE o.observed_at > now() - interval '24 hours' \
	  GROUP BY p.strategy_id, o.reason \
	) \
	SELECT strategy_id, \
	       COALESCE(SUM(n) FILTER (WHERE reason='hit_tp'), 0)       AS hit_tp, \
	       COALESCE(SUM(n) FILTER (WHERE reason='hit_sl'), 0)       AS hit_sl, \
	       COALESCE(SUM(n) FILTER (WHERE reason='hit_horizon'), 0)  AS hit_horizon, \
	       COALESCE(SUM(n) FILTER (WHERE reason='funding_flip'), 0) AS funding_flip, \
	       COALESCE(SUM(n) FILTER (WHERE reason='orphan_flat_close'), 0) AS orphan, \
	       SUM(n) AS total, \
	       ROUND(100.0 * COALESCE(SUM(n) FILTER (WHERE reason IN ('hit_tp','hit_sl')), 0) / NULLIF(SUM(n),0), 1) AS pct_tpsl_protected, \
	       SUM(pnl) AS net_pnl_usd \
	FROM win_loss \
	GROUP BY strategy_id ORDER BY net_pnl_usd DESC;"

.PHONY: stats
stats: ## Quick state summary (counts per major table)
	@$(PSQL) -c "SELECT 'trades' AS k, COUNT(*) FROM market_trades \
		UNION ALL SELECT 'orderbook', COUNT(*) FROM market_orderbook_snapshots \
		UNION ALL SELECT 'ticker', COUNT(*) FROM market_ticker_snapshots \
		UNION ALL SELECT 'raw_documents', COUNT(*) FROM raw_documents \
		UNION ALL SELECT 'predictions', COUNT(*) FROM predictions \
		UNION ALL SELECT 'paper_positions', COUNT(*) FROM paper_positions \
		UNION ALL SELECT 'outcomes', COUNT(*) FROM outcomes \
		UNION ALL SELECT 'wallet_snapshots', COUNT(*) FROM wallet_snapshots \
		UNION ALL SELECT 'lab_experiments', COUNT(*) FROM lab_experiments \
		UNION ALL SELECT 'lab_evaluations', COUNT(*) FROM lab_evaluations \
		UNION ALL SELECT 'mutation_proposals', COUNT(*) FROM mutation_proposals \
		UNION ALL SELECT 'bist_symbols', COUNT(*) FROM bist_symbols \
		UNION ALL SELECT 'us_symbols', COUNT(*) FROM us_symbols \
		UNION ALL SELECT 'market_bars', COUNT(*) FROM market_bars \
		ORDER BY k;"

.PHONY: bist-seed
bist-seed: ## Discover BIST symbols from live feed (first boot: activates all)
	$(DC) $(DC_DEV) exec ingestion-market uv run matrix-bist-symbols --bootstrap-active

.PHONY: bist-discover
bist-discover: ## Refresh BIST symbol metadata (new listings only; active flags unchanged)
	$(DC) $(DC_DEV) exec ingestion-market uv run matrix-bist-symbols

.PHONY: bist-poll
bist-poll: ## Run one BIST bar poll cycle and exit
	$(DC) $(DC_DEV) exec ingestion-market uv run matrix-bist-bars --once

.PHONY: graph-backfill-once
graph-backfill-once: ## One backfill batch: re-extract heuristic docs via agent path
	$(DC) $(DC_DEV) exec graph uv run python -m graph.main --backfill-once

.PHONY: bist-bars-backfill
bist-bars-backfill: ## Backfill BIST 1m bars (last 5d via yfinance; works off-session)
	$(DC) $(DC_DEV) exec ingestion-market uv run matrix-bist-bars --once --interval 1m --period 5d

.PHONY: bist-stats
bist-stats: ## BIST-specific counts (symbols, bars by interval, predictions)
	@$(PSQL) -c "SELECT 'bist_symbols.active' AS k, COUNT(*) FROM bist_symbols WHERE active \
		UNION ALL SELECT 'bars.1m', COUNT(*) FROM market_bars WHERE asset_class='bist' AND interval='1m' \
		UNION ALL SELECT 'bars.1d', COUNT(*) FROM market_bars WHERE asset_class='bist' AND interval='1d' \
		UNION ALL SELECT 'predictions.bist', COUNT(*) FROM predictions WHERE asset_class='bist' \
		UNION ALL SELECT 'paper_positions.bist', COUNT(*) FROM paper_positions WHERE asset_class='bist' \
		ORDER BY k;"

.PHONY: us-seed
us-seed: ## Discover US symbols (S&P500 + Nasdaq-100; first boot: activates all)
	$(DC) $(DC_DEV) exec ingestion-market uv run matrix-us-symbols --bootstrap-active

.PHONY: us-discover
us-discover: ## Refresh US symbol metadata (new listings only; active flags unchanged)
	$(DC) $(DC_DEV) exec ingestion-market uv run matrix-us-symbols

.PHONY: us-poll
us-poll: ## Run one US bar poll cycle and exit
	$(DC) $(DC_DEV) exec ingestion-market uv run matrix-us-bars --once

.PHONY: us-bars-backfill
us-bars-backfill: ## Backfill US 1m bars (last 5d via yfinance; works off-session)
	$(DC) $(DC_DEV) exec ingestion-market uv run matrix-us-bars --once --interval 1m --period 5d

.PHONY: us-stats
us-stats: ## US-specific counts (symbols, bars by interval, predictions)
	@$(PSQL) -c "SELECT 'us_symbols.active' AS k, COUNT(*) FROM us_symbols WHERE active \
		UNION ALL SELECT 'bars.1m', COUNT(*) FROM market_bars WHERE asset_class='us' AND interval='1m' \
		UNION ALL SELECT 'bars.1d', COUNT(*) FROM market_bars WHERE asset_class='us' AND interval='1d' \
		UNION ALL SELECT 'predictions.us', COUNT(*) FROM predictions WHERE asset_class='us' \
		UNION ALL SELECT 'paper_positions.us', COUNT(*) FROM paper_positions WHERE asset_class='us' \
		ORDER BY k;"

##@ Service shells

.PHONY: shell-agent
shell-agent: ## /bin/bash inside the agent container
	$(DC) $(DC_BASE) exec agent bash

.PHONY: shell-labs
shell-labs: ## /bin/bash inside the labs container
	$(DC) $(DC_BASE) exec labs bash

##@ Labs ops

.PHONY: lab-scan
lab-scan: ## Scan for a promotion proposal
	$(DC) $(DC_BASE) exec labs uv run python -m labs.main --scan-once --min-evals 5

.PHONY: lab-apply-best
lab-apply-best: ## Apply the most recent pending lab_promotion proposal
	$(DC) $(DC_BASE) exec labs uv run python -m labs.main --apply-best

.PHONY: universe-scan-once
universe-scan-once: ## Score+reconcile the tradable universe once (MARKET=crypto|bist|all; shadow unless UNIVERSE_MANAGER_ENFORCE=true)
	$(DC) $(DC_BASE) exec labs uv run python -m labs.main --universe-once --universe-asset-class $${MARKET:-all}

.PHONY: universe-status
universe-status: ## Show scored/active tradable universe (top 80 by score)
	@$(PSQL_SHARED) -c "SELECT asset_class, symbol, active, round(score::numeric,4) AS score, liquidity_usd, rank, became_active_at, last_scored_at FROM tradable_symbols WHERE active OR score > 0 ORDER BY asset_class, score DESC NULLS LAST LIMIT 80"

.PHONY: bist-seed-universe
bist-seed-universe: ## Alias for bist-seed (dynamic discover, no embedded list)
	$(MAKE) bist-seed

.PHONY: cert-scan
cert-scan: ## Scan active strategies; auto-grant paper_trade_certificate where eligible
	$(DC) $(DC_BASE) exec reflection uv run python -m reflection.main --scan-grants

.PHONY: method-ab
method-ab: ## Realised PnL by decision method (rule / llm / llm+rule / conflict / +explore / +lesson), last DAYS (default 7)
	$(PSQL_SHARED) -c "SELECT coalesce(p.context->>'method','(none)') AS method, count(*) AS n, round(sum(o.pnl_usd),2) AS pnl_usd, round(avg((o.pnl_usd>0)::int),3) AS win_rate, round(avg(o.pnl_usd),4) AS avg_pnl FROM outcomes o JOIN predictions p ON p.id=o.prediction_id WHERE p.strategy_id='matrix_agent' AND o.observed_at >= now() - make_interval(days => $(or $(DAYS),7)) AND o.reason <> 'orphan_flat_close' GROUP BY 1 ORDER BY pnl_usd;"

.PHONY: ranker-ab
ranker-ab: ## Counterfactual: realised pnl% of traded signals vs virtual pnl% of signals the ranker skipped (last DAYS, default 7)
	$(PSQL_SHARED) -c "SELECT p.strategy_id, p.asset_class, count(o.id) AS n_traded, round(avg(o.pnl_pct)*10000,2) AS traded_bps, count(*) FILTER (WHERE p.context->'virtual_outcome' IS NOT NULL) AS n_skipped, round(avg((p.context->'virtual_outcome'->>'pnl_pct')::numeric) FILTER (WHERE p.context->'virtual_outcome' IS NOT NULL)*10000,2) AS skipped_bps FROM predictions p LEFT JOIN outcomes o ON o.prediction_id=p.id AND o.reason<>'orphan_flat_close' WHERE p.created_at >= now() - make_interval(days => $(or $(DAYS),7)) AND p.side IN ('long','short') GROUP BY 1,2 ORDER BY 2,1;"

.PHONY: director-once
director-once: ## Run one Director tick now (digest + LLM review + brief)
	$(DC) $(DC_BASE) exec director uv run python -m director.main --once

.PHONY: director-digest
director-digest: ## Print the deterministic system digest (no LLM)
	$(DC) $(DC_BASE) exec director uv run python -m director.main --digest-only

.PHONY: director-tail
director-tail: ## Follow Director briefs/actions
	$(DC) $(DC_BASE) logs -f --tail=100 director

.PHONY: notify-tail
notify-tail: ## Follow the Telegram notify daemon logs (dry-run if token unset)
	$(DC) $(DC_BASE) logs -f --tail=100 notify

.PHONY: bulletin-once
bulletin-once: ## Generate one bulletin draft (template/LLM); prints the slug
	$(DC) $(DC_BASE) exec bulletin uv run python -m bulletin.main --once

.PHONY: bulletin-list
bulletin-list: ## List recent bulletin issues with status
	$(DC) $(DC_BASE) exec bulletin uv run python -m bulletin.main --list

.PHONY: bulletin-publish
bulletin-publish: ## Publish a draft. Usage: make bulletin-publish SLUG=<slug>
	@if [ -z "$(SLUG)" ]; then echo "Usage: make bulletin-publish SLUG=<slug>" && exit 1; fi
	$(DC) $(DC_BASE) exec bulletin uv run python -m bulletin.main --publish $(SLUG)

.PHONY: diagnose-matrix-agent
diagnose-matrix-agent: ## Horizon/threshold sweep + signal attribution for matrix_agent
	$(DC) $(DC_BASE) exec backtest uv run matrix-backtest-diagnose \
		$${SYMBOL:+--symbol=$${SYMBOL}} --days=$${DAYS:-7}

.PHONY: suggest
suggest: ## AI param suggester. Args: STRATEGY (default grid) SYMBOL DAYS RISK SAMPLES TOP_K
	$(DC) $(DC_BASE) exec backtest uv run matrix-backtest-suggester \
		--strategy=$${STRATEGY:-grid} \
		$${SYMBOL:+--symbol=$${SYMBOL}} \
		--days=$${DAYS:-7} \
		--risk=$${RISK:-balanced} \
		$${SAMPLES:+--samples $${SAMPLES}} \
		--top-k=$${TOP_K:-5}

##@ Dev Agent

.PHONY: dev-agent-tail
dev-agent-tail: ## Follow dev_agent logs
	$(DC) $(DC_DEV) logs -f dev_agent

.PHONY: dev-agent-queue
dev-agent-queue: ## Show review queue (awaiting_review + failed)
	$(PSQL) -c "SELECT * FROM dev_review_queue LIMIT 20;"

.PHONY: dev-agent-lessons
dev-agent-lessons: ## Show lesson stats (active only)
	$(PSQL) -c "SELECT * FROM dev_lesson_stats LIMIT 30;"

.PHONY: dev-agent-pause
dev-agent-pause: ## Global kill switch ON (usage: make dev-agent-pause REASON="...")
	$(PSQL) -c "UPDATE dev_agent_runtime SET paused=true, pause_reason='$(REASON)', paused_at=NOW(), paused_by='cli' WHERE id=true;"

.PHONY: dev-agent-resume
dev-agent-resume: ## Global kill switch OFF
	$(PSQL) -c "UPDATE dev_agent_runtime SET paused=false, pause_reason=NULL, paused_at=NULL, paused_by=NULL WHERE id=true;"

.PHONY: dev-agent-clean
dev-agent-clean: ## Remove worktrees of terminal tasks older than DAYS (default 7); merged ones are removed automatically
	$(DC) $(DC_DEV) exec dev-agent uv run python -m dev_agent.clean $(or $(DAYS),7)

.PHONY: dev-agent-index
dev-agent-index: ## Rebuild dev_codebase_nodes index (POST /codebase/reindex)
	@curl -sf -X POST http://agent.matrix.local/codebase/reindex | python3 -m json.tool

.PHONY: dev-agent-test
dev-agent-test: ## Run dev_agent test suite (excludes live/E2E)
	cd services/dev_agent && uv run pytest -v -m "not live"

.PHONY: brain-test
brain-test: ## Run brain pure-logic test suite (host, no DB/SDK)
	cd services/brain && uv run pytest -v

.PHONY: shared-test
shared-test: ## Run matrix-shared test suite (host)
	cd packages/python-shared && uv run --no-sync pytest -v

.PHONY: install-hooks
install-hooks: ## Install git hooks (pre-push + commit message sanitizer)
	cp infra/hooks/pre-push .git/hooks/pre-push
	cp infra/hooks/prepare-commit-msg .git/hooks/prepare-commit-msg
	cp infra/hooks/commit-msg .git/hooks/commit-msg
	chmod +x .git/hooks/pre-push .git/hooks/prepare-commit-msg .git/hooks/commit-msg
	@echo "✓ pre-push, prepare-commit-msg, commit-msg hooks installed"

##@ Backtest

.PHONY: backtest-historical
backtest-historical: ## Historical replay. Vars: STRATEGY SYMBOL DAYS N_GRIDS BAND HORIZON
	$(DC) $(DC_BASE) exec backtest uv run matrix-backtest-historical \
		--strategy=$${STRATEGY:-grid} \
		$${SYMBOL:+--symbol=$${SYMBOL}} \
		--asset-class=$${ASSET_CLASS:-crypto} \
		--days=$${DAYS:-7} \
		--n-grids=$${N_GRIDS:-10} \
		--price-band-pct=$${BAND:-0.02} \
		--horizon-s=$${HORIZON:-300}

##@ LLM backend

.PHONY: llm-bedrock
llm-bedrock: ## Route LLM through AWS Bedrock (efsora-admin profile -> .env)
	@./scripts/llm_backend.sh bedrock

.PHONY: llm-subscription
llm-subscription: ## Route LLM back through Claude Code subscription (OAuth)
	@./scripts/llm_backend.sh subscription

.PHONY: llm-cursor
llm-cursor: ## Route all LLM calls through Cursor Auto (`cursor agent login` or API key)
	@./scripts/llm_backend.sh cursor

.PHONY: claude-creds-sync
claude-creds-sync: ## Copy the host Claude Code OAuth session (access token only) into the matrix_claude_config volume
	@./scripts/claude_creds_sync.sh

.PHONY: claude-creds-install
claude-creds-install: ## Install the hourly launchd job that keeps container Claude auth fresh
	@mkdir -p ~/.matrix/bin && cp scripts/claude_creds_sync.sh ~/.matrix/bin/claude_creds_sync.sh && chmod +x ~/.matrix/bin/claude_creds_sync.sh
	@cp infra/launchd/com.matrix.claude-creds.plist ~/Library/LaunchAgents/ && launchctl unload ~/Library/LaunchAgents/com.matrix.claude-creds.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/com.matrix.claude-creds.plist && echo "✓ com.matrix.claude-creds loaded (every 30 min; script copied to ~/.matrix/bin — launchd cannot execute from ~/Documents)"

.PHONY: llm-openrouter
llm-openrouter: ## Make OpenRouter (free models) the primary LLM backend; subscription stays as fallback
	@./scripts/llm_backend.sh openrouter

.PHONY: openrouter-models
openrouter-models: ## List currently free OpenRouter models (tool-calling ones first)
	$(DC) $(DC_BASE) exec director uv run python -c "import asyncio,json; from matrix_shared.openrouter_llm import list_free_models; [print(('T ' if m['tools'] else '  ')+m['id'], m['context']) for m in asyncio.run(list_free_models())]"

.PHONY: cursor-login-docker
cursor-login-docker: ## One-time Cursor CLI login (matrix-agent volume → graph/dev_agent too)
	@./scripts/cursor_login_docker.sh

.PHONY: lock-llm-services
lock-llm-services: ## Refresh uv.lock for services using matrix-shared LLM (after dep changes)
	@for svc in synthesis graph reflection brain agent backtest dev_agent director; do \
	  echo "==> $$svc"; \
	  docker compose run --rm --no-deps --entrypoint "" \
	    -v "$$(pwd)/packages/python-shared:/workspace/packages/python-shared" \
	    -v "$$(pwd)/services/$$svc:/workspace/services/$$svc" \
	    $$svc sh -c "cd /workspace/services/$$svc && uv lock -q && uv sync -q"; \
	done

.PHONY: llm-haiku
llm-haiku: ## Set default tier to Haiku (decision/feeder/bulletin; quality loops stay Sonnet)
	@./scripts/llm_backend.sh haiku

.PHONY: llm-sonnet
llm-sonnet: ## Set default tier back to Sonnet
	@./scripts/llm_backend.sh sonnet

.PHONY: bybit-test-order
bybit-test-order: ## Place one small market order on Bybit testnet (BTCUSDT 0.001)
	$(DC) $(DC_DEV) exec backtest uv run python /workspace/scripts/bybit_test_order.py

.PHONY: trade-status
trade-status: ## Show Bybit network + live execution gate from .env
	@./scripts/trade_status.sh

.PHONY: llm-status
llm-status: ## Show current LLM backend + default tier (from .env)
	@./scripts/llm_backend.sh status

##@ Reliability

LAUNCH_AGENTS := $(HOME)/Library/LaunchAgents
WATCHDOG_PLISTS := com.matrix.watchdog com.matrix.caffeinate
# ~/Documents is TCC-protected; launchd cannot exec scripts there. Copy the
# self-contained watchdog to an unprotected support dir and run it from there.
WATCHDOG_DIR := $(HOME)/Library/Application Support/matrix
WATCHDOG_SCRIPT := $(WATCHDOG_DIR)/stall_watchdog.sh

.PHONY: watchdog-install
watchdog-install: ## Install launchd stall-watchdog + caffeinate (prevents suspend-freeze hangs)
	@mkdir -p "$(LAUNCH_AGENTS)" "$(HOME)/Library/Logs" "$(WATCHDOG_DIR)"
	@cp scripts/stall_watchdog.sh "$(WATCHDOG_SCRIPT)"
	@chmod +x "$(WATCHDOG_SCRIPT)"
	@for name in $(WATCHDOG_PLISTS); do \
		sed -e "s|__SCRIPT__|$(WATCHDOG_SCRIPT)|g" -e "s|__HOME__|$(HOME)|g" \
			"infra/launchd/$$name.plist" > "$(LAUNCH_AGENTS)/$$name.plist"; \
		launchctl unload "$(LAUNCH_AGENTS)/$$name.plist" 2>/dev/null || true; \
		launchctl load "$(LAUNCH_AGENTS)/$$name.plist"; \
		echo "loaded $$name"; \
	done
	@echo "watchdog: runs every 5min; log at $(HOME)/Library/Logs/matrix-watchdog.log"

.PHONY: watchdog-uninstall
watchdog-uninstall: ## Remove launchd stall-watchdog + caffeinate
	@for name in $(WATCHDOG_PLISTS); do \
		launchctl unload "$(LAUNCH_AGENTS)/$$name.plist" 2>/dev/null || true; \
		rm -f "$(LAUNCH_AGENTS)/$$name.plist"; \
		echo "removed $$name"; \
	done
	@rm -f "$(WATCHDOG_SCRIPT)"

.PHONY: watchdog-status
watchdog-status: ## Show watchdog state + recent actions
	@launchctl list | grep -E "com\.matrix\.(watchdog|caffeinate)" || echo "not installed"
	@echo "--- last watchdog actions ---"
	@tail -n 15 "$(HOME)/Library/Logs/matrix-watchdog.log" 2>/dev/null || echo "(no log yet)"

.PHONY: watchdog-run
watchdog-run: ## Run the stall watchdog once now (restarts hung services)
	@./scripts/stall_watchdog.sh

##@ Cleanup

.PHONY: clean
clean: ## Remove dangling build artifacts (no data loss)
	docker image prune -f

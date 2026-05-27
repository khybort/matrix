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
DC_DEV      := $(DC_BASE) -f docker-compose.dev.yml
DC_PROD     := $(DC_BASE) -f docker-compose.prod.yml
DC_LOCAL    := $(DC_BASE) -f docker-compose.dev.yml -f docker-compose.local.yml

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
		UNION ALL SELECT 'market_bars', COUNT(*) FROM market_bars \
		ORDER BY k;"

.PHONY: bist-seed
bist-seed: ## Seed the BIST symbol universe (idempotent)
	$(DC) $(DC_BASE) exec ingestion uv run python -m ingestion.bist.symbols --once

.PHONY: bist-poll
bist-poll: ## Run one BIST bar poll cycle and exit
	$(DC) $(DC_BASE) exec ingestion uv run python -m ingestion.bist.bars --once

.PHONY: bist-stats
bist-stats: ## BIST-specific counts (symbols, bars by interval, predictions)
	@$(PSQL) -c "SELECT 'bist_symbols.active' AS k, COUNT(*) FROM bist_symbols WHERE active \
		UNION ALL SELECT 'bars.1m', COUNT(*) FROM market_bars WHERE asset_class='bist' AND interval='1m' \
		UNION ALL SELECT 'bars.1d', COUNT(*) FROM market_bars WHERE asset_class='bist' AND interval='1d' \
		UNION ALL SELECT 'predictions.bist', COUNT(*) FROM predictions WHERE asset_class='bist' \
		UNION ALL SELECT 'paper_positions.bist', COUNT(*) FROM paper_positions WHERE asset_class='bist' \
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

.PHONY: bist-seed-universe
bist-seed-universe: ## One-shot: upsert the embedded BIST symbol universe into bist_symbols
	$(DC) $(DC_BASE) exec ingestion uv run matrix-bist-symbols

.PHONY: cert-scan
cert-scan: ## Scan active strategies; auto-grant paper_trade_certificate where eligible
	$(DC) $(DC_BASE) exec reflection uv run python -m reflection.main --scan-grants

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
		--symbol=$${SYMBOL:-BTCUSDT} --days=$${DAYS:-7}

.PHONY: suggest
suggest: ## AI param suggester. Args: STRATEGY (default grid) SYMBOL DAYS RISK SAMPLES TOP_K
	$(DC) $(DC_BASE) exec backtest uv run matrix-backtest-suggester \
		--strategy=$${STRATEGY:-grid} \
		--symbol=$${SYMBOL:-BTCUSDT} \
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
dev-agent-clean: ## Apply worktree cleanup policy (manual; cron is intentionally absent)
	$(DC) $(DC_DEV) exec dev_agent uv run python -m dev_agent.tools.clean

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
install-hooks: ## Install git hooks (pre-push: blocks dev-agent/* branches)
	cp infra/hooks/pre-push .git/hooks/pre-push
	chmod +x .git/hooks/pre-push
	@echo "✓ pre-push hook installed"

##@ Backtest

.PHONY: backtest-historical
backtest-historical: ## Historical replay. Vars: STRATEGY SYMBOL DAYS N_GRIDS BAND HORIZON
	$(DC) $(DC_BASE) exec backtest uv run matrix-backtest-historical \
		--strategy=$${STRATEGY:-grid} \
		--symbol=$${SYMBOL:-BTCUSDT} \
		--asset-class=$${ASSET_CLASS:-crypto} \
		--days=$${DAYS:-7} \
		--n-grids=$${N_GRIDS:-10} \
		--price-band-pct=$${BAND:-0.02} \
		--horizon-s=$${HORIZON:-300}

##@ Cleanup

.PHONY: clean
clean: ## Remove dangling build artifacts (no data loss)
	docker image prune -f

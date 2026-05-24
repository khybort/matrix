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
#   make dashboard     # open http://localhost:3030

DC          := docker compose
DC_BASE     := -f docker-compose.yml
DC_DEV      := $(DC_BASE) -f docker-compose.dev.yml
DC_PROD     := $(DC_BASE) -f docker-compose.prod.yml

PSQL        := $(DC) exec postgres psql -U matrix -d matrix

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
build: ## Build all images (postgres + services + web)
	$(DC) $(DC_BASE) build

.PHONY: up
up: ## Start the base stack (no overrides)
	$(DC) $(DC_BASE) up -d

.PHONY: up-dev
up-dev: ## Start everything in dev mode (hot reload)
	$(DC) $(DC_DEV) up -d

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
migrate: ## Run alembic upgrade head via one-shot migrate container
	$(DC) $(DC_BASE) --profile migrate run --rm migrate upgrade head

.PHONY: migrate-down
migrate-down: ## Roll back the most recent migration
	$(DC) $(DC_BASE) --profile migrate run --rm migrate downgrade -1

.PHONY: migrate-new
migrate-new: ## Create a new migration: make migrate-new MSG="add foo table"
	$(DC) $(DC_BASE) --profile migrate run --rm migrate revision --autogenerate -m "$(MSG)"

.PHONY: psql
psql: ## Open a psql shell on the local postgres
	$(PSQL)

.PHONY: db-reset
db-reset: ## Truncate dynamic tables (predictions, paper_positions, outcomes, snapshots, lab_*)
	$(PSQL) -c "TRUNCATE market_trades, market_orderbook_snapshots, market_ticker_snapshots, predictions, paper_positions, outcomes, wallet_snapshots, mutation_proposals CASCADE; DELETE FROM lab_evaluations; DELETE FROM lab_experiments; UPDATE wallets SET cash_usd = starting_capital_usd, locked_usd = 0, circuit_tripped_at = NULL;"

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

.PHONY: dashboard
dashboard: ## Open the dashboard in your browser
	@open http://localhost:3030 2>/dev/null || xdg-open http://localhost:3030 2>/dev/null || \
		echo "Open http://localhost:3030 in your browser"

.PHONY: leaderboard
leaderboard: ## Print lab leaderboard
	$(DC) $(DC_BASE) exec labs uv run python -m labs.main --leaderboard

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

##@ Cleanup

.PHONY: clean
clean: ## Remove dangling build artifacts (no data loss)
	docker image prune -f

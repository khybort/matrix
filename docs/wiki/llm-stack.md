---
title: LLM stack
updated: 2026-09-19
sources: [packages/python-shared/src/matrix_shared/{subscription_llm,openrouter_llm,usage_ledger}.py, scripts/claude_creds_sync.sh]
status: current
---

How the services get a model, what it costs, and how that is bounded.

## Claims
- **Backend plan**: subscription (Claude Code CLI) → openrouter → rule-only.
  Every caller degrades gracefully; no LLM outage stops trading, it only makes
  decisions rule-based.
- **Subscription auth is a synced host session.** `claude setup-token` tokens
  returned 401 on this account, so `scripts/claude_creds_sync.sh` reads the
  macOS keychain entry, **strips the refresh token** (a container refreshing it
  would invalidate the host login) and writes `.credentials.json` into the
  `matrix_claude_config` volume mounted at `/root/.claude` in every LLM
  service. Runs every 30 min from `~/.matrix/bin` via launchd. `.env`'s
  `CLAUDE_CODE_OAUTH_TOKEN` is deliberately blank. Never print the token.
- **Concurrency is bounded across containers** by Postgres advisory locks
  (`MATRIX_LLM_GLOBAL_SLOTS`, default 4), because the constraint is the
  subscription's rate budget, not dollars.
- **Usage ledger**: every completion appends one JSON line to
  `~/.claude/matrix_usage/YYYY-MM-DD.jsonl` on the shared volume — service,
  session, model, turns, cost, error, reason, duration. `summary(days=)` gives
  per-service calls/cost/timeouts/p50/p95. This exists because logs are not
  queryable across containers and the `agent_usage` table was blocked on the
  migration chain.
- **Measured behaviour (2026-09-14)**: single-shot p50 ≈ 26 s, p95 ≈ 38 s
  through the CLI, ~20k tokens of harness overhead per call regardless of
  prompt size. Consequences: the call timeout is 60 s (45 s was discarding ~21 %
  of calls that would have completed), and batching 12 symbols per call is
  strictly cheaper than 4.
- **Budget alert**: notify warns when the day's ledger cost exceeds
  `MATRIX_LLM_DAILY_BUDGET_USD` (default 25). Typical day: ~$1–2.
- **Director** runs one hourly review; since 2026-09-14 it resumes its cadence
  from the ledger instead of reviewing on every container restart.

# matrix-dev_agent

Self-improving development agent. Drives Claude Code via the Claude Agent SDK
in isolated git worktrees. `FORBIDDEN_PATHS` in `config.py` protects exactly the
three live-capital gate files (`matrix_shared/trading_safety.py`,
`matrix_shared/exchange_shadow.py`, `execution/safety.py`); everything else —
strategies, agent, execution adapters — is open to autonomous edits.

Since 2026-09-12 a completed task is **integrated**, not just labelled: the
runner runs pytest for every touched service in the worktree, commits, and
merges `dev-agent/task-N` into `main` (`DEV_AGENT_INTEGRATION=merge`, default)
unless the live repo has uncommitted edits to the same files or the merge
conflicts — then the branch is left `awaiting_review`. Failing tests fail the
task (`test_broke`) and feed the lesson synthesizer. Merged worktrees are
removed automatically. Merging into the bind-mounted repo is the deploy:
watchfiles reloads the touched services.

## Quick start

```bash
make build
make migrate
make up-dev
```

Check it's alive:

```bash
curl localhost:8009/healthz
make dev-agent-tail
```

## File a task

From any Claude Code session in this repo:

```
/dev-task "<task description>"
```

Or via curl:

```bash
curl -X POST localhost:8009/tasks \
  -H 'content-type: application/json' \
  -d '{"description":"add a comment to README","source":"manual","auto_commit":false}'
```

Defaults: `auto_commit=false`, `auto_pr=false`, `run_tests=true`, `max_turns=50`,
`cost_cap_usd=5.00`. Override any of them in the POST body.

## Review a task

```bash
make dev-agent-queue           # show awaiting_review + failed
make psql -- -c "SELECT * FROM dev_task_events WHERE task_id=<id> ORDER BY seq;"

# Inspect the worktree:
cd worktrees/dev-agent/task-<id>
git diff main

# Accept / discard / revise:
/dev-task accept <id>
/dev-task discard <id>
/dev-task revise <id> "please also handle X"
```

## Lessons (the learning loop)

Failed tasks generate **draft** lessons. A lesson becomes active when you approve
it, or automatically once the same topic has recurred twice
(`DEV_AGENT_LESSON_AUTO_ACTIVATE_REPEATS`):

```bash
curl localhost:8009/lessons?status=draft | jq
/dev-task lesson-approve <id>
```

Active lessons are retrieved via text match against the next task's description
and `touches_files` overlap, and injected into the agent's system prompt
under the `## LESSONS` section.

## Operations

```bash
make dev-agent-pause REASON="too many failures"
make dev-agent-resume
make dev-agent-clean                   # apply worktree cleanup policy
make dev-agent-test                    # unit + integration suite
```

Live acceptance tests (cost real $):

```bash
export ANTHROPIC_API_KEY=sk-...
make up-dev
cd services/dev_agent && uv run pytest -v -m live
```

## Safety

Hard rules — none of them can be turned off via config:

- **Path edits**: `FORBIDDEN_PATHS` in `config.py` lists the three live-capital
  gate files; Edit/Write to them fails the task with
  `failure_reason='trading_path_violation'`. Everything else is permitted.
- **Live capital**: enforced in `services/execution` (paper-trade certificate,
  kill switch via `matrix_shared.trading_safety`) — independent of dev_agent
  path policy.
- **`dev-agent/*` branches** cannot be pushed (`infra/hooks/pre-push`).
  Install with `make install-hooks`.
- **Per-task cost cap** defaults to $5 (enforced from the SDK's final result
  cost); **daily cap** $50 pauses picking for the rest of the UTC day. Env vars:
  `DEV_AGENT_TASK_COST_CAP_USD`, `DEV_AGENT_DAILY_COST_CAP_USD`.
- **Heartbeat reaper** runs every worker iteration; tasks silent > 120s are
  marked `failed/stale_heartbeat` (the reaper label — distinguishable from a real crash inside the run). Tasks whose worktree can't be created fail
  (`worktree_failed`) instead of running against the live repo.
- **All new lessons** enter as `draft` and only influence future tasks after
  explicit `/dev-task lesson-approve <id>`.
- **Tool loop guard**: 5 consecutive identical tool calls fails the task.
- **Heartbeat reaper**: tasks whose heartbeat hasn't updated in 2 minutes are
  marked `failed` (reason `stale_heartbeat`).

## Architecture

See the design spec: `docs/superpowers/specs/2026-05-25-dev-agent-design.md`.
Implementation plan: `docs/superpowers/plans/2026-05-25-dev-agent.md`.

```
.claude/commands/dev-task.md    → curl POST /tasks
.claude/commands/dev-handoff.md → curl POST /tasks (with conversation_snapshot)

dev_agent FastAPI (port 8009):
  - REST: /tasks, /lessons, /runtime/*
  - Worker loop: pick (FOR UPDATE SKIP LOCKED) → worktree → SDK run → status

Postgres LOCAL tier:
  dev_tasks, dev_task_runs, dev_task_events, dev_agent_lessons,
  dev_agent_runtime, dev_codebase_nodes (Phase 1+)

worktrees/dev-agent/task-<id>/  ← host filesystem, mounted RW into container
```

## Module layout

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI app + worker loop, env config wiring |
| `api.py` | REST routes (`build_app(pool)`) |
| `config.py` | env-driven config + **hardcoded** `FORBIDDEN_PATHS` |
| `db.py` | asyncpg pool factory |
| `worker.py` | queue picker, lifecycle driver, retry helper |
| `sdk_runner.py` | Claude Agent SDK call wrapper with safety hooks + event streaming |
| `safety.py` | `check_tool_call`, `CostCap`, `ToolLoopDetector`, `ForbiddenPathError` |
| `worktree.py` | git worktree create/cleanup, `assert_no_unauthorized_commits` |
| `runtime.py` | global pause/resume, heartbeat, reaper |
| `memory.py` | lessons store (draft/active/archived), text retrieval |
| `lesson_synth.py` | failure → draft lesson (Haiku LLM) |
| `prompt.py` | 5-layer system prompt builder |
| `events.py` | stream events to `dev_task_events` |

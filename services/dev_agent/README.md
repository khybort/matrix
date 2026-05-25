# matrix-dev_agent

Self-improving development agent. Drives Claude Code via the Claude Agent SDK
in isolated git worktrees. Default-deny: trading paths (`services/strategy/`,
`services/agent/`, `services/execution/`) are hardcoded blocked.

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

Failed tasks generate **draft** lessons. They do not influence future runs until
you approve them:

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

- **Trading paths** blocked at the `can_use_tool` boundary (`services/dev_agent/safety.py`).
  Edit/Write into `services/strategy/`, `services/agent/`, or `services/execution/`
  immediately fails the task with `failure_reason='trading_path_violation'`.
  Read/Grep/Glob into those paths is allowed.
- **`dev-agent/*` branches** cannot be pushed (`infra/hooks/pre-push`).
  Install with `make install-hooks`.
- **Per-task cost cap** defaults to $5; daily cap to $50. Env vars:
  `DEV_AGENT_TASK_COST_CAP_USD`, `DEV_AGENT_DAILY_COST_CAP_USD`.
- **All new lessons** enter as `draft` and only influence future tasks after
  explicit `/dev-task lesson-approve <id>`.
- **Tool loop guard**: 5 consecutive identical tool calls fails the task.
- **Heartbeat reaper**: tasks whose heartbeat hasn't updated in 2 minutes are
  marked `failed` (reason `worker_crash`).

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

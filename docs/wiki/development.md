---
title: Development
updated: 2026-09-19
sources: [docs/ENGINEERING_LESSONS.md, docker-compose.dev.yml, packages/python-shared/src/matrix_shared/dev_watchfiles.py]
status: current
---

How to change this codebase without breaking the running system — the tree is
live, and another author's uncommitted work shares it.

## Claims
- **Editing `src/` deploys immediately.** Dev compose bind-mounts source and the
  entrypoint restarts the service on save. Editing `packages/python-shared`
  restarts ~15 containers at once; batch shared-library edits.
- **The entrypoint also supervises.** Since 2026-09-19
  `matrix_shared/dev_watchfiles.py` restarts a child that exits on its own
  (2→60 s backoff, logged). Before that a crashed worker left a healthy-looking
  empty container — three days of no trading ([[incidents]]).
- **Tests run inside the service image** (the host has no `uv`):
  `docker run --rm --entrypoint uv matrix-<svc>:local run --no-sync pytest`
  with `src/` and `tests/` bind-mounted. Service directories use underscores,
  images use hyphens (`agent_lessons` → `matrix-agent-lessons`). The ingestion
  image has no pytest; add `--with pytest --with pytest-asyncio`.
- **Tests must not touch live state.** Use the synthetic `test` asset class and
  its own wallets (`services/backtest/tests/isolated_market.py`), an isolated
  database for dev_agent, a private advisory-lock namespace for rate-limit
  tests, and always clean up seeded predictions and slot rows.
- **Staging rule — the tree contains foreign uncommitted work.** Never
  `git add -A`. For a file that also carries someone else's edits: snapshot
  before editing, then stage HEAD plus only your own hunks
  (`git hash-object -w` + `git update-index --cacheinfo`). Changelog entries go
  in through the same mechanism.
- **Migrations**: `make migrate` (local) and
  `SHARED_DATABASE_URL=... make migrate-shared`. Do not add a revision while an
  uncommitted `00NN_*.py` sits in the tree.
- **The dev_agent writes code too.** It works in a git worktree, runs the
  affected services' tests, commits and merges into main on green. Its patches
  are reviewable in `dev_tasks`/`dev_task_runs`; forbidden paths in
  [[risk-gates]].

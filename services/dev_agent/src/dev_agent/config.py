"""dev_agent configuration — env-driven, with one HARDCODED constant.

FORBIDDEN_PATHS is intentionally a top-level tuple. It is NOT loaded from
env vars or a YAML file. Changing it requires a code edit and a commit.

2026-05-26: opened by operator directive so the learning loop can mutate
strategy / agent / execution code. 2026-09-12: re-closed for exactly the
live-capital gate files (see FORBIDDEN_PATHS) — the agent can improve how
we trade, never whether a real order is allowed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# 2026-09-12 (docs/AUTONOMY_PLAN.md P4.6): dev_agent may edit strategies,
# the agent and execution adapters, but NOT the three files that stand
# between the paper engine and real money. Those change only via a human
# commit. Everything else in the worktree is open.
# 2026-10-09: + the composite gate itself (live_gate.py, which the other two
# delegate to and which had been left open) and the two-leg carry executor
# (carry_executor.py, carry_venues.py: the only other code that can build and
# send orders).
FORBIDDEN_PATHS: tuple[str, ...] = (
    "packages/python-shared/src/matrix_shared/trading_safety.py",
    "packages/python-shared/src/matrix_shared/exchange_shadow.py",
    "services/execution/src/execution/safety.py",
    "packages/python-shared/src/matrix_shared/live_gate.py",
    "packages/python-shared/src/matrix_shared/carry_executor.py",
    "packages/python-shared/src/matrix_shared/carry_venues.py",
)


@dataclass(frozen=True)
class Config:
    database_url: str
    parallel: int
    daily_cost_cap_usd: float
    task_cost_cap_usd: float
    repo_root: str
    worktree_root: str
    port: int


def load_config() -> Config:
    # No ANTHROPIC_API_KEY here: dev_agent runs the `claude` CLI under the
    # user's Claude Code subscription. Auth flows via CLAUDE_CODE_OAUTH_TOKEN
    # (set in compose), which the spawned CLI reads itself.
    return Config(
        database_url=_require_env("LOCAL_DATABASE_URL"),
        parallel=int(os.environ.get("DEV_AGENT_PARALLEL", "2")),
        daily_cost_cap_usd=float(os.environ.get("DEV_AGENT_DAILY_COST_CAP_USD", "50")),
        task_cost_cap_usd=float(os.environ.get("DEV_AGENT_TASK_COST_CAP_USD", "5")),
        repo_root=os.environ.get("DEV_AGENT_REPO_ROOT", "/workspace"),
        worktree_root=os.environ.get("DEV_AGENT_WORKTREE_ROOT", "/workspace/worktrees/dev-agent"),
        port=int(os.environ.get("DEV_AGENT_PORT", "8009")),
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required env var missing: {name}")
    return value

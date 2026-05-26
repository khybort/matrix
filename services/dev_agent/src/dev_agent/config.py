"""dev_agent configuration — env-driven, with one HARDCODED constant.

FORBIDDEN_PATHS is intentionally a top-level tuple. It is NOT loaded from
env vars or a YAML file. Changing it requires a code edit and a commit.

2026-05-26: opened by operator directive. The autonomous learning loop
needs dev_agent to be able to mutate strategy / agent / execution code
based on its own outcome feedback. The runtime trading-safety wall
(LIVE_EXECUTION_REQUIRES_CERT, has_valid_certificate, should_submit_live
in matrix_shared.trading_safety) is independent of this gate and still
blocks live order submission until a paper-trade certificate is granted.

Rollback path: re-populate the tuple with the three service prefixes
below, commit, redeploy dev_agent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Empty since 2026-05-26 — dev_agent autonomous edits permitted everywhere.
# Live execution safety enforced separately at runtime (trading_safety.py).
FORBIDDEN_PATHS: tuple[str, ...] = ()


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

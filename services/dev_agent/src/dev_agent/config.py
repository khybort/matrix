"""dev_agent configuration — env-driven, with one HARDCODED constant.

FORBIDDEN_PATHS is intentionally a top-level tuple. It is NOT loaded from
env vars or a YAML file. The spec mandates that trading-path access can
not be unlocked by any flag, env var, or runtime setting. Changing this
requires a code edit and a commit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Hardcoded. Do not load from config. Do not add an unlock mechanism.
FORBIDDEN_PATHS: tuple[str, ...] = (
    "services/strategy/",
    "services/agent/",
    "services/execution/",
)


@dataclass(frozen=True)
class Config:
    database_url: str
    anthropic_api_key: str
    parallel: int
    daily_cost_cap_usd: float
    task_cost_cap_usd: float
    repo_root: str
    worktree_root: str
    port: int


def load_config() -> Config:
    return Config(
        database_url=_require_env("LOCAL_DATABASE_URL"),
        anthropic_api_key=_require_env("ANTHROPIC_API_KEY"),
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

"""Brain configuration — env-driven."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    local_database_url: str
    shared_database_url: str
    port: int
    model: str
    max_turns: int


def load_config() -> Config:
    local = _require_env("LOCAL_DATABASE_URL")
    # SHARED falls back to LOCAL in single-PC dev (matches matrix_shared.config).
    shared = os.environ.get("SHARED_DATABASE_URL") or local
    return Config(
        local_database_url=local,
        shared_database_url=shared,
        port=int(os.environ.get("BRAIN_PORT", "3032")),
        model=os.environ.get("BRAIN_MODEL", "claude-opus-4-7"),
        max_turns=int(os.environ.get("BRAIN_MAX_TURNS", "12")),
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required env var missing: {name}")
    return value

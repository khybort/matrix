"""Brain configuration — env-driven."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _default_sonnet() -> str:
    """Late binding so the env-resolved MATRIX_MODEL_SONNET is read at
    `load_config()` time, not at import time."""
    from matrix_shared.subscription_llm import MODEL_SONNET
    return MODEL_SONNET


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
        # Default Sonnet 4.6: most chat questions don't need Opus, and Opus on
        # every tool-loop step burns the rate budget the trading loop also
        # draws from. Set BRAIN_MODEL=<id> for a global override; per-request
        # override via {"model":"<id>"} in the chat body. The default resolves
        # through subscription_llm so Bedrock/Vertex MATRIX_MODEL_SONNET
        # overrides land here automatically.
        model=os.environ.get("BRAIN_MODEL")
        or _default_sonnet(),
        max_turns=int(os.environ.get("BRAIN_MAX_TURNS", "12")),
    )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required env var missing: {name}")
    return value

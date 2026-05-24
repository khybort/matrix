"""Centralized settings loaded from environment / .env.local."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root() -> Path:
    """Walk up until we find the repo marker (CLAUDE.md at root)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CLAUDE.md").exists() and (parent / "docker-compose.yml").exists():
            return parent
    return Path.cwd()


REPO_ROOT = _find_repo_root()


class Settings(BaseSettings):
    """All env-driven config. Reads from .env.local at repo root."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Database — two-tier (see docs/ARCHITECTURE.md for the split rationale).
    # - LOCAL: hot path (market data, raw_documents, AGE graph) — always
    #   a local Postgres container with AGE extension.
    # - SHARED: cold path (predictions, wallet, lab, strategy_configs) —
    #   can be Neon in multi-PC setups, defaults to local for single-PC.
    # `database_url` (legacy) aliases local for backward compat until all
    # services are explicitly tier-aware.
    database_url: str = Field(
        default="postgres://matrix:matrix_dev_only@localhost:5432/matrix",
        description="Legacy single-URL setting. Aliases local_database_url.",
    )
    local_database_url: str | None = None
    shared_database_url: str | None = None

    # LLM
    ai_gateway_api_key: str | None = None

    # Exchanges — testnet
    bybit_testnet_api_key: str | None = None
    bybit_testnet_api_secret: str | None = None

    # Exchanges — mainnet (live trading)
    bybit_api_key: str | None = None
    bybit_api_secret: str | None = None

    # Risk caps (see docs/TRADING.md — never bypass)
    max_position_pct: float = 2.0
    daily_loss_circuit_pct: float = 5.0
    live_capital_cap_usd: float = 2000.0
    live_execution_enabled: bool = False

    # Node identity
    node_id: str = Field(default="unknown-node")
    node_roles: str = Field(
        default="dev",
        description="Comma-separated role list (ingestion,graph,strategy,...)",
    )

    @staticmethod
    def _normalize(url: str) -> str:
        """SQLAlchemy expects postgresql+asyncpg://; normalize common shorthand.

        asyncpg does not accept libpq-style query params like ?sslmode=require
        or ?channel_binding=require (those are psycopg conventions). Neon URLs
        ship with them, so we strip them here. SSL is enforced by passing
        ssl='require' to asyncpg via the engine's connect_args (in db.py).
        """
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

        parts = urlsplit(url)
        if parts.query:
            kept = [
                (k, v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in {"sslmode", "channel_binding"}
            ]
            url = urlunsplit(parts._replace(query=urlencode(kept)))
        return url

    @property
    def sqlalchemy_url(self) -> str:
        """Backward-compat: aliases local tier."""
        return self.sqlalchemy_local_url

    @property
    def sqlalchemy_local_url(self) -> str:
        """Local tier (market data, AGE graph, raw_documents)."""
        return self._normalize(self.local_database_url or self.database_url)

    @property
    def sqlalchemy_shared_url(self) -> str:
        """Shared tier (predictions, wallet, lab, strategy_configs).
        Falls back to local if SHARED_DATABASE_URL is not set."""
        return self._normalize(self.shared_database_url or self.local_database_url or self.database_url)

    def roles(self) -> set[str]:
        return {r.strip() for r in self.node_roles.split(",") if r.strip()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

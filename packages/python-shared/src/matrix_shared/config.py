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

    # Database
    database_url: str = Field(
        default="postgres://matrix:matrix_dev_only@localhost:5432/matrix",
        description="Postgres async URL. Note: SQLAlchemy needs postgresql+asyncpg scheme.",
    )

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

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy expects postgresql+asyncpg://; normalize from common shorthand."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    def roles(self) -> set[str]:
        return {r.strip() for r in self.node_roles.split(",") if r.strip()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

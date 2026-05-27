"""asyncpg pools for both DB tiers the Brain reads from."""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from brain.config import Config


def _libpq_dsn(url: str) -> str:
    """Normalize a SQLAlchemy-style URL to a libpq DSN asyncpg accepts."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql://"):
        if url.startswith(prefix):
            return "postgres://" + url[len(prefix):]
    return url


@dataclass
class Pools:
    local: asyncpg.Pool
    shared: asyncpg.Pool

    def for_tier(self, tier: str) -> asyncpg.Pool:
        return self.local if tier == "local" else self.shared

    async def close(self) -> None:
        await self.local.close()
        if self.shared is not self.local:
            await self.shared.close()


async def make_pools(cfg: Config) -> Pools:
    local = await asyncpg.create_pool(
        dsn=_libpq_dsn(cfg.local_database_url), min_size=1, max_size=4, command_timeout=30
    )
    if cfg.shared_database_url == cfg.local_database_url:
        shared = local
    else:
        shared = await asyncpg.create_pool(
            dsn=_libpq_dsn(cfg.shared_database_url),
            min_size=1,
            max_size=4,
            command_timeout=30,
        )
    return Pools(local=local, shared=shared)

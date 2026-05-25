"""asyncpg pool wiring for dev_agent."""

from __future__ import annotations

import asyncpg

from dev_agent.config import Config


async def make_pool(cfg: Config) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=cfg.database_url,
        min_size=1,
        max_size=max(2, cfg.parallel * 2 + 2),
        command_timeout=30,
    )

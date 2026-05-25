"""Matrix dev_agent entry point — FastAPI + worker loop."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from loguru import logger

from dev_agent.api import build_app
from dev_agent.config import load_config
from dev_agent.db import make_pool
from dev_agent.runtime import is_paused
from dev_agent.worker import process_one_task


async def _worker_loop(pool, repo_root: Path, worktree_root: Path) -> None:
    # Lazy import so tests with fake_sdk don't need the real SDK installed.
    try:
        from claude_agent_sdk import query as real_query
        query_fn = real_query
    except ImportError:
        logger.warning("claude_agent_sdk not installed — worker will idle")
        query_fn = None

    while True:
        try:
            if await is_paused(pool):
                await asyncio.sleep(5)
                continue
            handled = await process_one_task(
                pool=pool,
                repo_root=repo_root,
                worktree_root=worktree_root,
                query_fn=query_fn,
            )
            if not handled:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker loop error")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    pool = await make_pool(cfg)
    # Wire the API routes onto this app using the pool created here.
    inner = build_app(pool=pool)
    app.router.routes.extend(inner.router.routes)
    app.state.pool = pool
    app.state.cfg = cfg
    logger.info("dev_agent starting on port {}", cfg.port)
    worker_task = asyncio.create_task(
        _worker_loop(pool, Path(cfg.repo_root), Path(cfg.worktree_root))
    )
    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        await pool.close()
        logger.info("dev_agent stopped")


app = FastAPI(title="matrix-dev-agent", lifespan=lifespan)


def main() -> None:
    cfg = load_config()
    uvicorn.run(
        "dev_agent.main:app",
        host="0.0.0.0",
        port=cfg.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

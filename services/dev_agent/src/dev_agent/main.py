"""Matrix dev_agent entry point — FastAPI + worker loop."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from loguru import logger

from dev_agent.api import build_app
from dev_agent.config import load_config
from dev_agent.db import make_pool
from dev_agent.runtime import daily_spend_usd, is_paused, reap_stuck_running
from dev_agent.worker import process_one_task

REAP_SILENCE_S = 120
INDEX_EVERY_S = float(os.environ.get("DEV_AGENT_INDEX_EVERY_S", "3600"))
_daily_cap_logged = False


async def _worker_loop(pool, repo_root: Path, worktree_root: Path, daily_cap_usd: float = 50.0) -> None:
    global _daily_cap_logged
    # Lazy import so tests with fake_sdk don't need the real SDK installed.
    try:
        from claude_agent_sdk import query as real_query
        query_fn = real_query
    except ImportError:
        logger.warning("claude_agent_sdk not installed — worker will idle")
        query_fn = None

    last_index = 0.0
    while True:
        try:
            # Keep the codebase index fresh so tasks get real file context
            # (was indexed by nothing and passed as "" — docs/AUTONOMY_PLAN.md §3.9).
            if time.monotonic() - last_index >= INDEX_EVERY_S:
                last_index = time.monotonic()
                try:
                    from dev_agent.codebase import index_codebase
                    n = await index_codebase(pool, repo_root)
                    logger.info(f"codebase index refreshed: {n} nodes")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"codebase index failed: {e}")
            reaped = await reap_stuck_running(pool, max_silence_seconds=REAP_SILENCE_S)
            if reaped:
                logger.warning(f"reaped {reaped} stuck running task(s) (heartbeat > {REAP_SILENCE_S}s)")
            if await is_paused(pool):
                await asyncio.sleep(5)
                continue
            spent = await daily_spend_usd(pool)
            if spent >= daily_cap_usd:
                if not _daily_cap_logged:
                    logger.warning(f"daily cost cap reached (${spent:.2f} >= ${daily_cap_usd:.2f}); idling")
                    _daily_cap_logged = True
                await asyncio.sleep(300)
                continue
            _daily_cap_logged = False
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
        _worker_loop(pool, Path(cfg.repo_root), Path(cfg.worktree_root), cfg.daily_cost_cap_usd)
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

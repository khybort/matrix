"""Matrix Brain entry point — FastAPI SSE chat service."""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from loguru import logger

from brain.api import build_app
from brain.config import load_config
from brain.db import make_pools
from brain.runtime import BrainRuntime


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    pools = await make_pools(cfg)
    runtime = BrainRuntime(pools, model=cfg.model, max_turns=cfg.max_turns)
    inner = build_app(shared_pool=pools.shared, runtime=runtime)
    app.router.routes.extend(inner.router.routes)
    app.state.pools = pools
    app.state.cfg = cfg
    logger.info("brain starting on port {} (model={})", cfg.port, cfg.model)
    try:
        yield
    finally:
        await pools.close()
        logger.info("brain stopped")


app = FastAPI(title="matrix-brain", lifespan=lifespan)


def main() -> None:
    cfg = load_config()
    uvicorn.run("brain.main:app", host="0.0.0.0", port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()

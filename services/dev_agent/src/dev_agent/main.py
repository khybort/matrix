"""Matrix dev_agent entry point — starts FastAPI + worker loop."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from loguru import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("dev_agent starting")
    worker_task = asyncio.create_task(_worker_loop_placeholder())
    try:
        yield
    finally:
        logger.info("dev_agent stopping")
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


async def _worker_loop_placeholder() -> None:
    while True:
        logger.debug("worker tick (placeholder — no queue wired yet)")
        await asyncio.sleep(2)


app = FastAPI(title="matrix-dev-agent", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    uvicorn.run(
        "dev_agent.main:app",
        host="0.0.0.0",
        port=8009,
        log_level="info",
    )


if __name__ == "__main__":
    main()

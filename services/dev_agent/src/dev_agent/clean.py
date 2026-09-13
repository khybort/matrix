"""`make dev-agent-clean`: remove worktrees of terminal tasks older than N days.

integrate.py removes merged worktrees immediately; this sweeps the rest
(failed / discarded / awaiting_review that nobody picked up)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from loguru import logger

from dev_agent.config import load_config
from dev_agent.db import make_pool
from dev_agent.worktree import WorktreeManager


async def clean(days: int) -> int:
    cfg = load_config()
    pool = await make_pool(cfg)
    try:
        rows = await pool.fetch(
            "SELECT id FROM dev_tasks WHERE status IN ('merged','discarded','failed') "
            "AND finished_at < NOW() - make_interval(days => $1) AND worktree_path IS NOT NULL",
            days,
        )
    finally:
        await pool.close()
    mgr = WorktreeManager(repo_root=Path(cfg.repo_root), worktree_root=Path(cfg.worktree_root))
    n = 0
    for r in rows:
        try:
            mgr.cleanup(int(r["id"]))
            n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"cleanup task #{r['id']} failed: {e}")
    logger.info(f"removed {n} worktree(s) for terminal tasks older than {days}d")
    return n


def main() -> None:
    days = int(os.environ.get("DEV_AGENT_CLEAN_DAYS", sys.argv[1] if len(sys.argv) > 1 else "7"))
    asyncio.run(clean(days))


if __name__ == "__main__":
    main()

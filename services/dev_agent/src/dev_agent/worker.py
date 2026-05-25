"""Worker loop: pick → run → finalize. Phase 0 wiring.

The picker uses FOR UPDATE SKIP LOCKED to coordinate multiple worker
processes safely (Matrix pattern). Each pick happens inside a transaction;
the caller must commit/rollback.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import asyncpg

from dev_agent.runtime import beat_heartbeat
from dev_agent.sdk_runner import run_task_with_query
from dev_agent.worktree import (
    WorktreeManager,
    assert_no_unauthorized_commits,
)


async def pick_next_task(conn: asyncpg.Connection) -> dict | None:
    """Pick one pending task that doesn't conflict with running tasks.

    A pending task is skipped if any running task:
      - has exclusive=TRUE, OR
      - has touches_files that overlap (array intersection) with this task's.
    """
    row = await conn.fetchrow("""
        SELECT *
        FROM dev_tasks t
        WHERE t.status = 'pending'
          AND t.scheduled_for <= NOW()
          AND NOT EXISTS (
              SELECT 1 FROM dev_tasks r
              WHERE r.status = 'running'
                AND (r.exclusive = TRUE OR r.touches_files && t.touches_files)
          )
        ORDER BY t.priority DESC, t.created_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    """)
    return dict(row) if row else None


async def process_one_task(
    *,
    pool: asyncpg.Pool,
    repo_root: Path,
    worktree_root: Path,
    query_fn: Any,
) -> bool:
    """Pick one task and drive it to a terminal status. Returns True if a task
    was handled (regardless of outcome), False if the queue was empty."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            task = await pick_next_task(conn)
            if not task:
                return False
            await conn.execute(
                """UPDATE dev_tasks
                   SET status='running',
                       started_at=NOW(),
                       heartbeat_at=NOW()
                   WHERE id=$1""",
                task["id"],
            )

    task_id = task["id"]
    run_id = await pool.fetchval(
        """INSERT INTO dev_task_runs (task_id, run_number, status)
           VALUES ($1, COALESCE(
               (SELECT MAX(run_number)+1 FROM dev_task_runs WHERE task_id=$1),
               1
           ), 'running')
           RETURNING id""",
        task_id,
    )

    wt = None
    try:
        _ensure_test_repo(repo_root)
        wt_mgr = WorktreeManager(repo_root=repo_root, worktree_root=worktree_root)
        wt = wt_mgr.create(task_id=task_id, base_branch=task["base_branch"])
    except Exception:
        wt = None

    await beat_heartbeat(pool, task_id)

    # --- build prompt with retrieved lessons ---
    from dev_agent.memory import search_lessons_text
    from dev_agent.prompt import build_system_prompt

    lessons = await search_lessons_text(
        pool,
        query=task["description"],
        top_k=5,
        paths=list(task["touches_files"] or []),
    )

    snapshot = task.get("conversation_snapshot") if isinstance(task, dict) else task["conversation_snapshot"]
    if isinstance(snapshot, str):
        import json
        snapshot = json.loads(snapshot)

    system_prompt = build_system_prompt(
        task_description=task["description"],
        lessons=lessons,
        codebase_ctx="",
        auto_commit=task["auto_commit"],
        auto_pr=task["auto_pr"],
        run_tests=task["run_tests"],
        max_turns=task["max_turns"],
        handoff_snapshot=snapshot,
        touches_files=list(task["touches_files"] or []),
    )

    # Build ClaudeAgentOptions ONLY when query_fn is the real SDK. Tests pass
    # a fake query_fn that takes (prompt, options) with a sync 2-arg can_use
    # — the real-SDK path's async 3-arg wrapper would break those callers.
    options = None
    try:
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk import query as real_query
        if query_fn is real_query:
            options = ClaudeAgentOptions(
                cwd=str(wt.path) if wt else None,
                system_prompt=system_prompt,
                allowed_tools=["Bash", "Read", "Edit", "Write", "Grep", "Glob"],
                permission_mode="acceptEdits",
                max_turns=task["max_turns"],
                model=task["model"],
            )
    except ImportError:
        pass

    result = await run_task_with_query(
        pool=pool,
        task_id=task_id,
        run_id=run_id,
        cwd=wt.path if wt else worktree_root,
        max_turns=task["max_turns"],
        cost_cap_usd=float(task["cost_cap_usd"]),
        scenario=None,
        query_fn=query_fn or _empty_query,
        prompt=task["description"],
        options=options,
    )

    if wt is not None:
        try:
            assert_no_unauthorized_commits(wt, auto_commit=task["auto_commit"])
        except Exception:
            pass

    # review_mode picks the post-completion terminal state for successful runs:
    #   auto   → 'merged' (reviewed_by='auto'), skipping human review queue
    #   manual → 'awaiting_review' (today's behavior; needs POST /accept|/discard)
    # Failed tasks ignore this — failure has its own queue, lesson_synth path,
    # and a human probably wants to see what broke.
    if result.completed:
        final_status = "merged" if task.get("review_mode", "auto") == "auto" else "awaiting_review"
    else:
        final_status = "failed"
    auto_review = result.completed and task.get("review_mode", "auto") == "auto"
    await pool.execute(
        """UPDATE dev_tasks
           SET status=$1, finished_at=NOW(),
               failure_reason=$2, total_cost_usd=$3, total_tokens=$4,
               worktree_path=$5,
               reviewed_at = CASE WHEN $7::bool THEN NOW() ELSE reviewed_at END,
               reviewed_by = CASE WHEN $7::bool THEN 'auto'  ELSE reviewed_by END
           WHERE id=$6""",
        final_status, result.failure_reason, result.total_cost_usd, result.total_tokens,
        str(wt.path) if wt else None, task_id, auto_review,
    )
    await pool.execute(
        """UPDATE dev_task_runs
           SET status=$1, finished_at=NOW(),
               failure_reason=$2, cost_usd=$3, tokens=$4,
               event_count=$5
           WHERE id=$6""",
        final_status, result.failure_reason, result.total_cost_usd, result.total_tokens,
        result.event_count, run_id,
    )

    if final_status == "failed":
        try:
            from dev_agent.lesson_synth import (
                maybe_synthesize_lesson_for_failure,
                real_haiku_llm,
            )
            await maybe_synthesize_lesson_for_failure(
                pool, task_id=task_id, llm=real_haiku_llm,
            )
        except Exception:
            from loguru import logger
            logger.exception("lesson synth failed (non-fatal)")

    return True


async def _empty_query(prompt, options, **_):
    if False:
        yield  # pragma: no cover


def _ensure_test_repo(repo_root: Path) -> None:
    """For lifecycle tests, init a tiny git repo so worktree create works."""
    if not repo_root.exists():
        repo_root.mkdir(parents=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_root), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=str(repo_root), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo_root), check=True, capture_output=True)
        (repo_root / "seed.txt").write_text("init\n")
        subprocess.run(["git", "add", "."], cwd=str(repo_root), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_root), check=True, capture_output=True)


async def mark_needs_changes_and_requeue(
    pool: asyncpg.Pool, task_id: int, notes: str
) -> int:
    """User asked for a revision. Move the task back to pending; the next
    worker pick will create a new dev_task_runs row (run_number ++)."""
    next_run = await pool.fetchval(
        """SELECT COALESCE(MAX(run_number), 0) + 1
           FROM dev_task_runs WHERE task_id = $1""",
        task_id,
    )
    await pool.execute(
        """UPDATE dev_tasks
           SET status='pending',
               review_notes=$1,
               reviewed_at=NOW(),
               cancel_requested=FALSE,
               finished_at=NULL
           WHERE id=$2""",
        notes, task_id,
    )
    return next_run

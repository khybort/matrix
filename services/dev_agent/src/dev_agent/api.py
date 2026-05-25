"""FastAPI routes for dev_agent."""

from __future__ import annotations

import json
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    description: str
    source: str = "manual"
    priority: int = 0
    acceptance: str | None = None
    touches_files: list[str] = Field(default_factory=list)
    exclusive: bool = False
    auto_commit: bool = False
    auto_pr: bool = False
    run_tests: bool = True
    max_turns: int = 50
    model: str = "claude-haiku-4-5"
    base_branch: str = "main"
    commit_message: str | None = None
    cost_cap_usd: float = 5.00
    conversation_snapshot: dict[str, Any] | None = None


def build_app(pool: asyncpg.Pool) -> FastAPI:
    app = FastAPI(title="matrix-dev-agent")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/tasks", status_code=201)
    async def create_task(payload: TaskCreate) -> dict[str, Any]:
        snapshot_json = (
            json.dumps(payload.conversation_snapshot)
            if payload.conversation_snapshot is not None
            else None
        )
        row = await pool.fetchrow(
            """
            INSERT INTO dev_tasks (
              source, description, priority, acceptance, touches_files, exclusive,
              auto_commit, auto_pr, run_tests, max_turns, model, base_branch,
              commit_message, cost_cap_usd, conversation_snapshot
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb)
            RETURNING id, status
            """,
            payload.source, payload.description, payload.priority, payload.acceptance,
            payload.touches_files, payload.exclusive,
            payload.auto_commit, payload.auto_pr, payload.run_tests, payload.max_turns,
            payload.model, payload.base_branch, payload.commit_message,
            payload.cost_cap_usd, snapshot_json,
        )
        return {"id": row["id"], "status": row["status"]}

    @app.get("/tasks")
    async def list_tasks(status: str | None = Query(None)) -> list[dict[str, Any]]:
        if status:
            rows = await pool.fetch(
                "SELECT id, status, description, finished_at, failure_reason "
                "FROM dev_tasks WHERE status=$1 ORDER BY id DESC LIMIT 100",
                status,
            )
        else:
            rows = await pool.fetch(
                "SELECT id, status, description, finished_at, failure_reason "
                "FROM dev_tasks ORDER BY id DESC LIMIT 100",
            )
        return [dict(r) for r in rows]

    @app.get("/tasks/{task_id}")
    async def get_task(task_id: int) -> dict[str, Any]:
        row = await pool.fetchrow("SELECT * FROM dev_tasks WHERE id=$1", task_id)
        if not row:
            raise HTTPException(status_code=404, detail="task not found")
        return dict(row)

    @app.post("/tasks/{task_id}/accept")
    async def accept(task_id: int, body: dict[str, Any]) -> dict[str, str]:
        await pool.execute(
            "UPDATE dev_tasks SET status='merged', reviewed_at=NOW(), reviewed_by=$1 "
            "WHERE id=$2",
            body.get("by", "user"), task_id,
        )
        return {"status": "merged"}

    @app.post("/tasks/{task_id}/discard")
    async def discard(task_id: int, body: dict[str, Any]) -> dict[str, str]:
        await pool.execute(
            "UPDATE dev_tasks SET status='discarded', reviewed_at=NOW(), reviewed_by=$1 "
            "WHERE id=$2",
            body.get("by", "user"), task_id,
        )
        return {"status": "discarded"}

    @app.post("/tasks/{task_id}/revise")
    async def revise(task_id: int, body: dict[str, Any]) -> dict[str, str]:
        from dev_agent.worker import mark_needs_changes_and_requeue
        await mark_needs_changes_and_requeue(pool, task_id, notes=body.get("notes", ""))
        return {"status": "pending"}

    @app.post("/tasks/{task_id}/kill")
    async def kill(task_id: int) -> dict[str, str]:
        await pool.execute("UPDATE dev_tasks SET cancel_requested=TRUE WHERE id=$1", task_id)
        return {"cancel_requested": "true"}

    @app.post("/runtime/pause")
    async def runtime_pause(body: dict[str, Any]) -> dict[str, str]:
        from dev_agent.runtime import pause
        await pause(pool, reason=body.get("reason", "manual"), actor=body.get("by", "user"))
        return {"paused": "true"}

    @app.post("/runtime/resume")
    async def runtime_resume() -> dict[str, str]:
        from dev_agent.runtime import resume
        await resume(pool)
        return {"paused": "false"}

    @app.post("/lessons/{lesson_id}/approve")
    async def lesson_approve(lesson_id: int, body: dict[str, Any]) -> dict[str, str]:
        from dev_agent.memory import approve_lesson
        await approve_lesson(pool, lesson_id=lesson_id, by=body.get("by", "user"))
        return {"status": "active"}

    @app.get("/lessons")
    async def list_lessons(status: str = Query("active")) -> list[dict[str, Any]]:
        from dev_agent.memory import list_active_lessons, list_draft_lessons
        if status == "draft":
            return await list_draft_lessons(pool)
        return await list_active_lessons(pool)

    return app

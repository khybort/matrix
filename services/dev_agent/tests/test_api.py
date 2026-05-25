"""tests/test_api.py — REST contract for /tasks"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from dev_agent.api import build_app

pytestmark = pytest.mark.asyncio


async def test_post_tasks_creates_pending(pg_pool):
    app = build_app(pool=pg_pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/tasks", json={
            "description": "do thing",
            "source": "manual",
            "auto_commit": False,
            "touches_files": ["a.py"],
        })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "pending"
        assert "id" in body
    row = await pg_pool.fetchrow("SELECT description, status FROM dev_tasks WHERE id=$1", body["id"])
    assert row["status"] == "pending"
    assert row["description"] == "do thing"


async def test_post_handoff_includes_snapshot(pg_pool):
    app = build_app(pool=pg_pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/tasks", json={
            "description": "finish what I started",
            "source": "handoff",
            "conversation_snapshot": {"messages": ["m1", "m2"]},
        })
        assert r.status_code == 201, r.text
    row = await pg_pool.fetchrow(
        "SELECT source, conversation_snapshot FROM dev_tasks ORDER BY id DESC LIMIT 1",
    )
    assert row["source"] == "handoff"
    # asyncpg may return JSONB as already-decoded dict OR as string depending on
    # codec. Accept both forms.
    snap = row["conversation_snapshot"]
    if isinstance(snap, str):
        import json
        snap = json.loads(snap)
    assert snap["messages"] == ["m1", "m2"]


async def test_review_queue_endpoint(pg_pool):
    await pg_pool.execute(
        "INSERT INTO dev_tasks (status, source, description) VALUES ('awaiting_review','manual','review me')"
    )
    app = build_app(pool=pg_pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/tasks?status=awaiting_review")
        assert r.status_code == 200
        items = r.json()
        assert any(t["description"] == "review me" for t in items)


async def test_kill_endpoint_sets_cancel_flag(pg_pool):
    task_id = await pg_pool.fetchval(
        "INSERT INTO dev_tasks (status, source, description) VALUES ('running','manual','x') RETURNING id"
    )
    app = build_app(pool=pg_pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(f"/tasks/{task_id}/kill")
        assert r.status_code == 200
    flag = await pg_pool.fetchval("SELECT cancel_requested FROM dev_tasks WHERE id=$1", task_id)
    assert flag is True


async def test_runtime_pause_resume(pg_pool):
    app = build_app(pool=pg_pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/runtime/pause", json={"reason": "test", "by": "ops"})
        assert r.status_code == 200
    paused = await pg_pool.fetchval("SELECT paused FROM dev_agent_runtime WHERE id=TRUE")
    assert paused is True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/runtime/resume")
        assert r.status_code == 200
    paused = await pg_pool.fetchval("SELECT paused FROM dev_agent_runtime WHERE id=TRUE")
    assert paused is False


async def test_lesson_approve_endpoint_promotes_draft(pg_pool):
    from dev_agent.memory import write_lesson_draft
    lid = await write_lesson_draft(
        pg_pool, source="failure", topic="t", summary="s",
        anti_pattern=None, correct_approach="ca",
        relevant_paths=[], origin_task_id=None,
    )
    app = build_app(pool=pg_pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(f"/lessons/{lid}/approve", json={"by": "user"})
        assert r.status_code == 200, r.text
    status = await pg_pool.fetchval("SELECT status FROM dev_agent_lessons WHERE id=$1", lid)
    assert status == "active"


async def test_lessons_list_filter_by_status(pg_pool):
    from dev_agent.memory import write_lesson_draft
    await write_lesson_draft(
        pg_pool, source="failure", topic="t1", summary="s",
        anti_pattern=None, correct_approach="ca",
        relevant_paths=[], origin_task_id=None,
    )
    app = build_app(pool=pg_pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/lessons?status=draft")
        assert r.status_code == 200
        items = r.json()
        assert any(l["topic"] == "t1" for l in items)

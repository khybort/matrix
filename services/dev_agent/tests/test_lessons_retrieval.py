"""tests/test_lessons_retrieval.py — lessons retrieval (no embeddings yet)."""

from __future__ import annotations

import pytest

from dev_agent.memory import (
    approve_lesson,
    list_active_lessons,
    list_draft_lessons,
    search_lessons_text,
    write_lesson_draft,
)

pytestmark = pytest.mark.asyncio


async def test_drafts_do_not_appear_in_active_listing(pg_pool):
    await write_lesson_draft(
        pg_pool,
        source="failure",
        topic="async-cleanup",
        summary="forgot to close pool",
        anti_pattern="left pool open across tests",
        correct_approach="use pytest fixture cleanup",
        relevant_paths=["services/dev_agent/"],
        origin_task_id=None,
    )
    active = await list_active_lessons(pg_pool)
    assert active == []
    drafts = await list_draft_lessons(pg_pool)
    assert len(drafts) == 1
    assert drafts[0]["topic"] == "async-cleanup"


async def test_approve_moves_draft_to_active(pg_pool):
    lid = await write_lesson_draft(
        pg_pool,
        source="user_correction",
        topic="t",
        summary="s",
        anti_pattern=None,
        correct_approach="ca",
        relevant_paths=[],
        origin_task_id=None,
    )
    await approve_lesson(pg_pool, lesson_id=lid, by="user")
    active = await list_active_lessons(pg_pool)
    assert len(active) == 1
    assert active[0]["id"] == lid
    drafts = await list_draft_lessons(pg_pool)
    assert drafts == []


async def test_search_text_returns_matching_active_lessons(pg_pool):
    lid = await write_lesson_draft(
        pg_pool, source="failure", topic="fastapi-deps",
        summary="forgot to inject session", anti_pattern="globals",
        correct_approach="use Depends", relevant_paths=["services/web/"],
        origin_task_id=None,
    )
    await approve_lesson(pg_pool, lesson_id=lid, by="user")
    hits = await search_lessons_text(pg_pool, query="fastapi", top_k=5)
    assert len(hits) == 1
    assert hits[0]["topic"] == "fastapi-deps"


async def test_search_text_skips_drafts(pg_pool):
    await write_lesson_draft(
        pg_pool, source="failure", topic="zzz",
        summary="never approved", anti_pattern=None,
        correct_approach="x", relevant_paths=[], origin_task_id=None,
    )
    hits = await search_lessons_text(pg_pool, query="zzz", top_k=5)
    assert hits == []

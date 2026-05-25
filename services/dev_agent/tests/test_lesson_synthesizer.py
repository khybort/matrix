"""tests/test_lesson_synthesizer.py — mocks the LLM."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dev_agent.lesson_synth import maybe_synthesize_lesson_for_failure

pytestmark = pytest.mark.asyncio


async def test_notable_failure_creates_draft(pg_pool):
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description, failure_reason, touches_files)
        VALUES ('failed','manual','some task','max_turns', ARRAY['services/graph/x.py'])
        RETURNING id
    """)
    fake_llm = AsyncMock(return_value={
        "topic": "scope-too-large",
        "summary": "task exceeded max_turns",
        "anti_pattern": "took on multi-file refactor in one go",
        "correct_approach": "split into discrete touches_files-scoped tasks",
        "relevant_paths": ["services/graph/"],
    })
    lesson_id = await maybe_synthesize_lesson_for_failure(
        pg_pool, task_id=task_id, llm=fake_llm,
    )
    assert lesson_id is not None
    fake_llm.assert_called_once()
    row = await pg_pool.fetchrow("SELECT status, topic FROM dev_agent_lessons WHERE id=$1", lesson_id)
    assert row["status"] == "draft"
    assert row["topic"] == "scope-too-large"


async def test_non_notable_failure_skipped(pg_pool):
    task_id = await pg_pool.fetchval("""
        INSERT INTO dev_tasks (status, source, description, failure_reason)
        VALUES ('failed','manual','x','api_unreachable')
        RETURNING id
    """)
    fake_llm = AsyncMock()
    result = await maybe_synthesize_lesson_for_failure(
        pg_pool, task_id=task_id, llm=fake_llm,
    )
    assert result is None
    fake_llm.assert_not_called()

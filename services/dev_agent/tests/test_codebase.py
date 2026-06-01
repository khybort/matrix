"""Tests for codebase index + retrieval."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_agent.codebase import index_codebase, search_codebase_context

pytestmark = pytest.mark.asyncio


async def test_index_and_search_codebase(pg_pool, tmp_path):
    src = tmp_path / "services" / "demo"
    src.mkdir(parents=True)
    (src / "hello.py").write_text('"""Demo module."""\ndef hello():\n    return "hi"\n')

    count = await index_codebase(pg_pool, tmp_path)
    assert count >= 2  # dir + file

    ctx = await search_codebase_context(
        pg_pool,
        query="hello demo module",
        paths=["services/demo/"],
        top_k=5,
    )
    assert "hello.py" in ctx
    assert "Demo module" in ctx

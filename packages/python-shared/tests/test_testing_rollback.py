"""`db_writes_rolled_back` runs every scope on one connection. Background work
spawned by code under test (edge_study's refresh task) used to open a session
on it while the caller's session was open; the caller's RELEASE then destroyed
the task's savepoint and the test failed ~1 run in 30. Runs against the live
local DB inside a rolled-back transaction."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _fresh_engines():
    from matrix_shared.db import reset_engines

    reset_engines()
    yield
    reset_engines()


@pytest.mark.asyncio
async def test_a_spawned_task_waits_for_the_holder_instead_of_interleaving_savepoints():
    from matrix_shared import db
    from matrix_shared.testing import db_writes_rolled_back

    order: list[str] = []

    async def background():
        async with db.shared_session_scope() as s:
            order.append("child in")
            await s.execute(text("SELECT 1"))
            await asyncio.sleep(0.05)
            await s.execute(text("SELECT 1"))
        order.append("child out")

    async with db_writes_rolled_back():
        async with db.shared_session_scope() as s:
            await s.execute(text("SELECT 1"))  # opens the caller's savepoint
            child = asyncio.create_task(background())
            await asyncio.sleep(0.02)  # the child would now be inside its own savepoint
            order.append("parent out")
        await child
        async with db.shared_session_scope() as s:  # the transaction is still usable
            assert (await s.execute(text("SELECT 1"))).scalar_one() == 1

    assert order == ["parent out", "child in", "child out"]


@pytest.mark.asyncio
async def test_a_task_spawned_under_the_scope_does_not_outlive_it():
    from matrix_shared.testing import db_writes_rolled_back

    async with db_writes_rolled_back():
        straggler = asyncio.create_task(asyncio.sleep(3600))
        await asyncio.sleep(0)
    assert straggler.cancelled()

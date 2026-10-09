"""Test-only helper: make a test's DB writes invisible and temporary.

Most service suites run against the LIVE databases (the stack has no separate
test schema), so a test that calls a global pass — slot scoring, certificate
revocation, a reflection tick, filing a dev task — mutates live rows, and a
committed 'pending' dev_tasks row gets claimed by the live worker.

`db_writes_rolled_back()` opens one connection per tier, begins an outer
transaction, and swaps every module-level reference to
`local_session_scope` / `shared_session_scope` / `session_scope` for a scope
whose sessions run in a SAVEPOINT on that connection. Code under test reads
its own writes; nothing is ever committed; the outer transaction is rolled
back on exit. Not covered: code that builds sessions or connections from the
engines directly, or that runs two scopes concurrently (one connection).
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


def _scope_on(conn: AsyncConnection):
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint",
                               expire_on_commit=False, autoflush=False)
        try:
            yield session
            await session.commit()  # releases the savepoint only
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    return scope


@asynccontextmanager
async def db_writes_rolled_back() -> AsyncIterator[None]:
    from matrix_shared import db

    local_engine, shared_engine = db.get_local_engine(), db.get_shared_engine()
    local = await local_engine.connect()
    await local.begin()
    if str(shared_engine.url) == str(local_engine.url):
        shared = local  # one DB: one transaction, so tiers see each other's writes
    else:
        shared = await shared_engine.connect()
        await shared.begin()
    originals = (db.local_session_scope, db.shared_session_scope)
    swap = {id(originals[0]): _scope_on(local), id(originals[1]): _scope_on(shared)}
    patched: list[tuple[object, str, object]] = []
    for mod in list(sys.modules.values()):
        attrs = getattr(mod, "__dict__", None)
        if not attrs:
            continue
        for name, val in list(attrs.items()):
            if id(val) in swap and any(val is o for o in originals):
                patched.append((mod, name, val))
                setattr(mod, name, swap[id(val)])
    try:
        yield
    finally:
        for mod, name, val in patched:
            setattr(mod, name, val)
        for conn in {id(local): local, id(shared): shared}.values():
            await conn.rollback()
            await conn.close()

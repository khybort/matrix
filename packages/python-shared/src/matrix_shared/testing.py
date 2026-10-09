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
engines directly.

One connection serves every scope, so two tasks must never hold sessions on it
at once: interleaved SAVEPOINT/RELEASE from two tasks aborts the transaction
("savepoint sa_savepoint_20 does not exist"). That is what code under test does
when it spawns background work — `edge_study.strategy_edge` fires a refresh
task that ran its study on this connection while `score_strategy_slots` held
its own session, and test_auto_cut_on_consecutive_losses failed ~1 run in 30
(2026-10-09). Sessions are therefore serialised per connection (re-entrant
within a task), and tasks spawned while the scope was active are cancelled
before the rollback, so none outlives the test into the next one's connection
or onto the live engine. A task that waits on a child needing the connection
while holding a session would deadlock; that raises after _GATE_TIMEOUT_S.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


_GATE_TIMEOUT_S = 60.0


class _ConnGate:
    """One task at a time on the shared connection; re-entrant per task."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0

    async def enter(self) -> None:
        me = asyncio.current_task()
        if self._owner is me:
            self._depth += 1
            return
        try:
            await asyncio.wait_for(self._lock.acquire(), _GATE_TIMEOUT_S)
        except TimeoutError:
            raise RuntimeError(
                "db_writes_rolled_back: a task waited on the test connection while "
                "another task held a session on it — the holder awaits work that "
                "needs the connection (gather/create_task inside a session scope)"
            ) from None
        self._owner, self._depth = me, 1

    def exit(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


def _scope_on(conn: AsyncConnection, gate: _ConnGate):
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        await gate.enter()
        try:
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
        finally:
            gate.exit()

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
    gates = {id(local): _ConnGate()}
    gates.setdefault(id(shared), _ConnGate())
    originals = (db.local_session_scope, db.shared_session_scope)
    swap = {id(originals[0]): _scope_on(local, gates[id(local)]),
            id(originals[1]): _scope_on(shared, gates[id(shared)])}
    tasks_before = asyncio.all_tasks()
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
        spawned = [t for t in asyncio.all_tasks() - tasks_before
                   if t is not asyncio.current_task() and not t.done()]
        for t in spawned:
            t.cancel()
        if spawned:
            await asyncio.gather(*spawned, return_exceptions=True)
        for mod, name, val in patched:
            setattr(mod, name, val)
        for conn in {id(local): local, id(shared): shared}.values():
            await conn.rollback()
            await conn.close()

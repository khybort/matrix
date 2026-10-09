import inspect
import os

import pytest_asyncio

os.environ.setdefault("SHARED_DATABASE_URL", "postgres://matrix:matrix_dev_only@localhost:5433/matrix_shared")
os.environ.setdefault("LOCAL_DATABASE_URL", "postgres://matrix:matrix_dev_only@localhost:5432/matrix")


@pytest_asyncio.fixture(autouse=True)
async def _db_writes_never_commit(request):
    """Tests here write through director.tools into LIVE tables: dev_tasks (the
    live dev_agent worker claims any committed 'pending' row — it once picked
    up a test task) and strategy_configs / paper_trade_certificate (the
    strategy dispatcher sees an 'active' row). Every async test runs in an
    outer transaction that is rolled back, so nothing it writes is committed."""
    if not inspect.iscoroutinefunction(request.function):
        yield
        return
    from matrix_shared.testing import db_writes_rolled_back

    async with db_writes_rolled_back():
        yield

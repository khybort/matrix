import inspect

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _db_writes_never_commit(request):
    """Labs tests apply proposals (new active / shadow strategy_configs rows,
    slot configs) in the LIVE shared tables the strategy dispatcher reads. Every async test runs in an outer transaction that is rolled
    back (matrix_shared.testing), so nothing it writes is ever committed or
    visible to the live services."""
    if not inspect.iscoroutinefunction(request.function):
        yield
        return
    from matrix_shared.testing import db_writes_rolled_back

    async with db_writes_rolled_back():
        yield

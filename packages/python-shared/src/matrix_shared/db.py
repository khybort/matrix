"""Two-tier async SQLAlchemy engines.

Two pools live alongside each other:
    LOCAL  — market data, raw_documents, AGE graph. Always points at the
             local docker postgres (config.local_database_url).
    SHARED — wallet, predictions, lab, strategy_configs. Points at Neon (or
             any shared Postgres) in multi-PC setups; falls back to LOCAL
             when SHARED_DATABASE_URL is unset.

Use the right session_scope for the table you're touching. See
docs/MULTI_PC_SETUP.md for the tier assignments per table.

Legacy `session_scope` is kept as an alias for `local_session_scope` so
older code that hasn't been tier-split yet keeps working — it just means
those writes go to the local DB. The tier-aware code paths route writes
explicitly via `shared_session_scope`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from matrix_shared.config import get_settings


def _create_engine(url: str) -> AsyncEngine:
    # asyncpg needs SSL passed as connect_arg, not URL query.
    # Neon (and most managed Postgres) require SSL; local docker does not.
    connect_args: dict = {}
    if ".neon.tech" in url or ".aws." in url or ".azure." in url:
        connect_args["ssl"] = "require"
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args=connect_args,
    )


@lru_cache(maxsize=1)
def get_local_engine() -> AsyncEngine:
    return _create_engine(get_settings().sqlalchemy_local_url)


@lru_cache(maxsize=1)
def get_shared_engine() -> AsyncEngine:
    return _create_engine(get_settings().sqlalchemy_shared_url)


@lru_cache(maxsize=1)
def _local_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_local_engine(), expire_on_commit=False, autoflush=False
    )


@lru_cache(maxsize=1)
def _shared_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_shared_engine(), expire_on_commit=False, autoflush=False
    )


# Legacy single-tier accessors (default to local). New code should be
# explicit via the tiered functions below.
def get_engine() -> AsyncEngine:
    return get_local_engine()


def get_session() -> AsyncSession:
    return _local_sessionmaker()()


def get_local_session() -> AsyncSession:
    return _local_sessionmaker()()


def get_shared_session() -> AsyncSession:
    return _shared_sessionmaker()()


@asynccontextmanager
async def local_session_scope() -> AsyncIterator[AsyncSession]:
    """Context-managed session against the LOCAL tier (commit on exit,
    rollback on error)."""
    session = _local_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def shared_session_scope() -> AsyncIterator[AsyncSession]:
    """Context-managed session against the SHARED tier."""
    session = _shared_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# Backward-compat. Existing import sites that haven't been tier-split yet
# (graph, ingestion) continue to use this; their tables already live on the
# local tier so the alias is correct.
session_scope = local_session_scope

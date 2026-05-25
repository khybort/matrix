"""Shared test fixtures for dev_agent.

Connects to the running matrix-postgres docker container (no separate ephemeral
test database — pytest-postgresql can't spin one up because the host doesn't
have postgresql binaries, only the docker container does).

Per-test isolation: each function-scope test starts with all dev_agent tables
truncated and the singleton runtime row reset to its default state.

Schema: a minimal subset of `infra/db/alembic/versions/0009_dev_agent.py` is
created on demand if not already present. We do NOT apply the full alembic
migration because it requires the migrate image (which has a known transitive
import bug — see commit 488db5d) and includes vector-extension columns
unrelated to Phase 0 unit tests.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
import pytest_asyncio

# Default points at the docker compose postgres exposed on localhost:5432.
# Override with DEV_AGENT_TEST_DSN env var.
TEST_DSN = os.environ.get(
    "DEV_AGENT_TEST_DSN",
    "postgres://matrix:matrix_dev_only@localhost:5432/matrix",
)


_MINIMAL_SCHEMA_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dev_task_status') THEN
        CREATE TYPE dev_task_status AS ENUM (
          'pending','running','awaiting_review','needs_changes',
          'merged','discarded','failed');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dev_task_source') THEN
        CREATE TYPE dev_task_source AS ENUM (
          'manual','handoff','roadmap','reflection','retry');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dev_event_type') THEN
        CREATE TYPE dev_event_type AS ENUM (
          'system','user','assistant_text','tool_use','tool_result',
          'thinking','error','status_change');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dev_lesson_source') THEN
        CREATE TYPE dev_lesson_source AS ENUM (
          'failure','needs_changes','self_reflection','user_correction','pnl_feedback');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dev_lesson_status') THEN
        CREATE TYPE dev_lesson_status AS ENUM ('draft','active','archived');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS dev_tasks (
  id BIGSERIAL PRIMARY KEY,
  status dev_task_status NOT NULL DEFAULT 'pending',
  source dev_task_source NOT NULL,
  priority SMALLINT NOT NULL DEFAULT 0,
  scheduled_for TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  description TEXT NOT NULL,
  acceptance TEXT,
  touches_files TEXT[] NOT NULL DEFAULT '{}',
  exclusive BOOLEAN NOT NULL DEFAULT FALSE,
  conversation_snapshot JSONB,
  auto_commit BOOLEAN NOT NULL DEFAULT FALSE,
  auto_pr BOOLEAN NOT NULL DEFAULT FALSE,
  run_tests BOOLEAN NOT NULL DEFAULT TRUE,
  max_turns INTEGER NOT NULL DEFAULT 50,
  model TEXT NOT NULL DEFAULT 'claude-haiku-4-5',
  base_branch TEXT NOT NULL DEFAULT 'main',
  commit_message TEXT,
  cost_cap_usd NUMERIC(10,4) NOT NULL DEFAULT 5.00,
  worktree_path TEXT,
  branch_name TEXT,
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  heartbeat_at TIMESTAMPTZ,
  test_results JSONB,
  pr_url TEXT,
  failure_reason TEXT,
  total_cost_usd NUMERIC(10,4),
  total_tokens INTEGER,
  review_notes TEXT,
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  parent_task_id BIGINT REFERENCES dev_tasks(id)
);
CREATE TABLE IF NOT EXISTS dev_task_runs (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES dev_tasks(id) ON DELETE CASCADE,
  run_number INTEGER NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  status dev_task_status NOT NULL,
  event_count INTEGER NOT NULL DEFAULT 0,
  cost_usd NUMERIC(10,4),
  tokens INTEGER,
  failure_reason TEXT,
  UNIQUE (task_id, run_number)
);
CREATE TABLE IF NOT EXISTS dev_task_events (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES dev_tasks(id) ON DELETE CASCADE,
  run_id BIGINT NOT NULL REFERENCES dev_task_runs(id),
  seq INTEGER NOT NULL,
  event_type dev_event_type NOT NULL,
  payload JSONB NOT NULL,
  cost_usd NUMERIC(10,6),
  tokens_in INTEGER,
  tokens_out INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (task_id, seq)
);
CREATE TABLE IF NOT EXISTS dev_agent_runtime (
  id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id = TRUE),
  paused BOOLEAN NOT NULL DEFAULT FALSE,
  pause_reason TEXT,
  paused_at TIMESTAMPTZ,
  paused_by TEXT
);
INSERT INTO dev_agent_runtime (id) VALUES (TRUE) ON CONFLICT DO NOTHING;

-- pgvector lives in ag_catalog. Use schema-qualified type to avoid
-- search_path gymnastics (ag_catalog.vector is always resolvable).
CREATE TABLE IF NOT EXISTS dev_agent_lessons (
  id BIGSERIAL PRIMARY KEY,
  status dev_lesson_status NOT NULL DEFAULT 'draft',
  source dev_lesson_source NOT NULL,
  topic TEXT NOT NULL,
  summary TEXT NOT NULL,
  anti_pattern TEXT,
  correct_approach TEXT NOT NULL,
  example_code TEXT,
  relevant_paths TEXT[] NOT NULL DEFAULT '{}',
  origin_task_id BIGINT REFERENCES dev_tasks(id),
  embedding ag_catalog.vector(1536),
  hit_count INTEGER NOT NULL DEFAULT 0,
  helpful_count INTEGER NOT NULL DEFAULT 0,
  superseded_by BIGINT REFERENCES dev_agent_lessons(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  approved_at TIMESTAMPTZ,
  approved_by TEXT
);
"""


@pytest_asyncio.fixture(scope="session")
async def _ensure_schema():
    """Apply minimal dev_agent schema once per test session (idempotent)."""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute(_MINIMAL_SCHEMA_SQL)
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def pg_pool(_ensure_schema):
    """Function-scoped pool with all dev_agent tables truncated."""
    pool = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=4)
    async with pool.acquire() as c:
        # Truncate everything, restart sequences. dev_agent_runtime is a
        # singleton — reset its mutable columns, don't delete the row.
        await c.execute("""
            TRUNCATE TABLE dev_agent_lessons, dev_task_events, dev_task_runs, dev_tasks
            RESTART IDENTITY CASCADE
        """)
        await c.execute("""
            UPDATE dev_agent_runtime
            SET paused = FALSE,
                pause_reason = NULL,
                paused_at = NULL,
                paused_by = NULL
            WHERE id = TRUE
        """)
    try:
        yield pool
    finally:
        await pool.close()

"""dev_agent — task queue, runs, events, lessons, runtime singleton, codebase nodes

Phase 0 of the self-improving dev agent. Tables live in LOCAL tier (hot path).
Trading-path access is enforced in code (services/dev_agent/safety.py), NOT
via a column on dev_tasks — hardcoded reject, no flag.

All DDL is emitted as raw SQL to avoid SQLAlchemy's enum-reference quirks
(sa.Enum with create_type=False emits CREATE TYPE x AS ENUM () instead of
referencing the existing type by name).

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-25

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # The pgvector type lives in ag_catalog (alongside Apache AGE). Setting
    # search_path for this transaction is enough for the CREATE TABLE
    # statements below; for runtime queries we use schema-qualified types
    # (ag_catalog.vector) so future Python code doesn't depend on whatever
    # session search_path the connection happens to inherit.
    op.execute('SET search_path = public, ag_catalog')

    # --- enums -------------------------------------------------------------
    op.execute("""
        CREATE TYPE dev_task_status AS ENUM (
          'pending', 'running', 'awaiting_review',
          'needs_changes', 'merged', 'discarded', 'failed'
        )
    """)
    op.execute("""
        CREATE TYPE dev_task_source AS ENUM (
          'manual', 'handoff', 'roadmap', 'reflection', 'retry'
        )
    """)
    op.execute("""
        CREATE TYPE dev_event_type AS ENUM (
          'system', 'user', 'assistant_text', 'tool_use',
          'tool_result', 'thinking', 'error', 'status_change'
        )
    """)
    op.execute("""
        CREATE TYPE dev_lesson_source AS ENUM (
          'failure', 'needs_changes', 'self_reflection',
          'user_correction', 'pnl_feedback'
        )
    """)
    op.execute("""
        CREATE TYPE dev_lesson_status AS ENUM ('draft', 'active', 'archived')
    """)

    # --- dev_tasks ---------------------------------------------------------
    op.execute("""
        CREATE TABLE dev_tasks (
          id              BIGSERIAL PRIMARY KEY,
          status          dev_task_status NOT NULL DEFAULT 'pending',
          source          dev_task_source NOT NULL,
          priority        SMALLINT NOT NULL DEFAULT 0,
          scheduled_for   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

          description     TEXT NOT NULL,
          acceptance      TEXT,
          touches_files   TEXT[] NOT NULL DEFAULT '{}',
          exclusive       BOOLEAN NOT NULL DEFAULT FALSE,
          conversation_snapshot JSONB,

          auto_commit     BOOLEAN NOT NULL DEFAULT FALSE,
          auto_pr         BOOLEAN NOT NULL DEFAULT FALSE,
          run_tests       BOOLEAN NOT NULL DEFAULT TRUE,
          max_turns       INTEGER NOT NULL DEFAULT 50,
          model           TEXT NOT NULL DEFAULT 'claude-haiku-4-5',
          base_branch     TEXT NOT NULL DEFAULT 'main',
          commit_message  TEXT,
          cost_cap_usd    NUMERIC(10,4) NOT NULL DEFAULT 5.00,

          worktree_path   TEXT,
          branch_name     TEXT,
          cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
          heartbeat_at    TIMESTAMPTZ,

          test_results    JSONB,
          pr_url          TEXT,
          failure_reason  TEXT,
          total_cost_usd  NUMERIC(10,4),
          total_tokens    INTEGER,

          review_notes    TEXT,
          reviewed_by     TEXT,
          reviewed_at     TIMESTAMPTZ,

          created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          started_at      TIMESTAMPTZ,
          finished_at     TIMESTAMPTZ,

          parent_task_id  BIGINT REFERENCES dev_tasks(id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_dev_tasks_picker
        ON dev_tasks (status, priority DESC, scheduled_for ASC)
        WHERE status = 'pending'
    """)
    op.execute("""
        CREATE INDEX idx_dev_tasks_touches
        ON dev_tasks USING GIN (touches_files)
        WHERE status = 'running'
    """)
    op.execute("""
        CREATE INDEX idx_dev_tasks_review_queue
        ON dev_tasks (status, finished_at DESC)
        WHERE status IN ('awaiting_review', 'failed')
    """)
    op.execute("""
        CREATE INDEX idx_dev_tasks_heartbeat
        ON dev_tasks (heartbeat_at)
        WHERE status = 'running'
    """)

    # --- dev_task_runs (before dev_task_events because of FK) --------------
    op.execute("""
        CREATE TABLE dev_task_runs (
          id              BIGSERIAL PRIMARY KEY,
          task_id         BIGINT NOT NULL REFERENCES dev_tasks(id) ON DELETE CASCADE,
          run_number      INTEGER NOT NULL,
          started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          finished_at     TIMESTAMPTZ,
          status          dev_task_status NOT NULL,
          event_count     INTEGER NOT NULL DEFAULT 0,
          cost_usd        NUMERIC(10,4),
          tokens          INTEGER,
          failure_reason  TEXT,
          CONSTRAINT uq_dev_task_runs_task_run UNIQUE (task_id, run_number)
        )
    """)

    # --- dev_task_events ---------------------------------------------------
    op.execute("""
        CREATE TABLE dev_task_events (
          id          BIGSERIAL PRIMARY KEY,
          task_id     BIGINT NOT NULL REFERENCES dev_tasks(id) ON DELETE CASCADE,
          run_id      BIGINT NOT NULL REFERENCES dev_task_runs(id),
          seq         INTEGER NOT NULL,
          event_type  dev_event_type NOT NULL,
          payload     JSONB NOT NULL,
          cost_usd    NUMERIC(10,6),
          tokens_in   INTEGER,
          tokens_out  INTEGER,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          CONSTRAINT uq_dev_task_events_task_seq UNIQUE (task_id, seq)
        )
    """)
    op.execute("CREATE INDEX idx_dev_task_events_task ON dev_task_events (task_id, seq)")
    op.execute("CREATE INDEX idx_dev_task_events_run ON dev_task_events (run_id, seq)")

    # --- dev_agent_lessons --------------------------------------------------
    op.execute("""
        CREATE TABLE dev_agent_lessons (
          id              BIGSERIAL PRIMARY KEY,
          status          dev_lesson_status NOT NULL DEFAULT 'draft',
          source          dev_lesson_source NOT NULL,
          topic           TEXT NOT NULL,
          summary         TEXT NOT NULL,
          anti_pattern    TEXT,
          correct_approach TEXT NOT NULL,
          example_code    TEXT,
          relevant_paths  TEXT[] NOT NULL DEFAULT '{}',
          origin_task_id  BIGINT REFERENCES dev_tasks(id),
          embedding       vector(1536),
          hit_count       INTEGER NOT NULL DEFAULT 0,
          helpful_count   INTEGER NOT NULL DEFAULT 0,
          superseded_by   BIGINT REFERENCES dev_agent_lessons(id),
          created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          approved_at     TIMESTAMPTZ,
          approved_by     TEXT
        )
    """)
    op.execute("""
        CREATE INDEX idx_dev_lessons_embedding
        ON dev_agent_lessons USING ivfflat (embedding vector_cosine_ops)
        WHERE status = 'active'
    """)
    op.execute("""
        CREATE INDEX idx_dev_lessons_paths
        ON dev_agent_lessons USING GIN (relevant_paths)
        WHERE status = 'active'
    """)

    # --- dev_codebase_nodes (Phase 1+ placeholder) -------------------------
    op.execute("""
        CREATE TABLE dev_codebase_nodes (
          id              BIGSERIAL PRIMARY KEY,
          path            TEXT NOT NULL UNIQUE,
          kind            TEXT NOT NULL,
          language        TEXT,
          loc             INTEGER,
          last_modified   TIMESTAMPTZ,
          summary         TEXT,
          embedding       vector(1536),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_dev_codebase_path ON dev_codebase_nodes (path)")
    op.execute("""
        CREATE INDEX idx_dev_codebase_embedding
        ON dev_codebase_nodes USING ivfflat (embedding vector_cosine_ops)
    """)

    # --- dev_agent_runtime (singleton) -------------------------------------
    op.execute("""
        CREATE TABLE dev_agent_runtime (
          id BOOLEAN PRIMARY KEY DEFAULT TRUE,
          paused BOOLEAN NOT NULL DEFAULT FALSE,
          pause_reason TEXT,
          paused_at TIMESTAMPTZ,
          paused_by TEXT,
          CONSTRAINT ck_dev_agent_runtime_singleton CHECK (id = TRUE)
        )
    """)
    op.execute("INSERT INTO dev_agent_runtime (id) VALUES (TRUE)")

    # --- views -------------------------------------------------------------
    op.execute("""
        CREATE VIEW dev_review_queue AS
        SELECT id, status, description, finished_at, test_results, worktree_path, failure_reason
        FROM dev_tasks
        WHERE status IN ('awaiting_review', 'failed')
        ORDER BY finished_at DESC
    """)
    op.execute("""
        CREATE VIEW dev_lesson_stats AS
        SELECT topic, COUNT(*) AS lesson_count, SUM(hit_count) AS total_hits,
               SUM(helpful_count) AS helpful, MAX(created_at) AS last_seen
        FROM dev_agent_lessons
        WHERE status = 'active'
        GROUP BY topic
        ORDER BY total_hits DESC
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS dev_lesson_stats")
    op.execute("DROP VIEW IF EXISTS dev_review_queue")
    op.execute("DROP TABLE IF EXISTS dev_agent_runtime")
    op.execute("DROP TABLE IF EXISTS dev_codebase_nodes")
    op.execute("DROP TABLE IF EXISTS dev_agent_lessons")
    op.execute("DROP TABLE IF EXISTS dev_task_events")
    op.execute("DROP TABLE IF EXISTS dev_task_runs")
    op.execute("DROP TABLE IF EXISTS dev_tasks")
    for enum_name in (
        "dev_lesson_status",
        "dev_lesson_source",
        "dev_event_type",
        "dev_task_source",
        "dev_task_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

-- Runs once on first container startup (Postgres entrypoint init.d).
-- The Dockerfile in infra/db/ builds an image with AGE + pgvector both installed.

-- AGE (graph)
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
CREATE EXTENSION IF NOT EXISTS age;
SELECT create_graph('matrix_graph');

-- pgvector (semantic search)
CREATE EXTENSION IF NOT EXISTS vector;

-- Useful built-ins
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

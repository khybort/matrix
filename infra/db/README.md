# infra/db

Database schema, migrations, and seed data for the shared Neon Postgres.

**Owner:** Machine A (Cortex). Machine B reads (generates types) but does not author.

## Stack

- **Neon Postgres** (managed; project to be provisioned via `vercel marketplace`)
- **pgvector** extension (semantic search)
- **Apache AGE** extension (graph)
- **Alembic** for migrations (Python-side ownership)

## Initial schema (Phase 1)

Tables (minimum viable):
- `raw_documents` — ingested artifacts, raw text + metadata
- `entities` — flattened mirror of graph nodes for relational joins
- `embeddings` — pgvector store
- `predictions` — emitted signals + provenance
- `outcomes` — observed market reactions + score
- `strategy_runs` — versioned strategy execution metadata

Graph (via AGE):
- `matrix_graph` graph: Company, Person, Event, Document, Asset, Concept, Prediction, Outcome nodes + their edges

## Setup (when scaffolded)

```bash
cd infra/db
uv sync
uv run alembic upgrade head
```

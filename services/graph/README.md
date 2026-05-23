# services/graph

Entity + relation extraction, graph upsert, hybrid retrieval API.

**Owner:** Machine A (Cortex)
**Language:** Python 3.13

## Responsibilities

- Consume `raw_documents` → extract entities (LLM-driven, Haiku for cost)
- Resolve entity references (dedup: same company, person, event)
- Upsert nodes + edges into Apache AGE graph
- Maintain embeddings table (pgvector) for semantic search
- Expose hybrid retrieval API (graph traversal + vector + full-text) for `services/strategy` and `apps/web`

## Key endpoints (to be built)

- `POST /extract` — synchronous extraction over a passed document
- `POST /retrieve` — hybrid retrieval for a query, returns context bundle
- `GET /graph/subgraph?root=<entity_id>&depth=2` — return JSON subgraph for visualization

## Setup (when scaffolded)

```bash
cd services/graph
uv sync
uv run python -m graph.main
```

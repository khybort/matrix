# packages/python-shared

Shared Python models, DB clients, and utilities consumed by all `services/*`.

**Owner:** Machine A (Cortex)

Includes:
- Pydantic models for core entities (Document, Prediction, Outcome, ...)
- SQLAlchemy session/engine factory
- LLM client wrapper (configured for Vercel AI Gateway)
- Embedding helpers
- Graph query helpers (AGE Cypher wrappers)

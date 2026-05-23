# services/strategy

Signal generation + prediction tracking + reflection loop.

**Owner:** Machine A (Cortex)
**Language:** Python 3.13

## Responsibilities

- House versioned strategy modules (each = a prompt template + retrieval recipe + scoring config)
- Schedule strategy runs (cron + event-driven)
- Emit `predictions` with full context provenance
- Daily reflection agent: aggregate scores → propose mutations → promote/retire

## Strategy module contract

```python
class Strategy(Protocol):
    id: str
    version: int
    horizon: timedelta  # outcome window
    async def generate(self, ctx: Context) -> list[Prediction]: ...
    def score(self, pred: Prediction, outcome: Outcome) -> float: ...
```

## First strategies (Phase 1-2)

1. `earnings_surprise_reaction_v1` — post-earnings drift
2. `guidance_change_v1` — reaction to forward-guidance updates
3. `insider_cluster_v1` — meaningful insider buying clusters (Form 4)

## Setup (when scaffolded)

```bash
cd services/strategy
uv sync
uv run python -m strategy.main
```

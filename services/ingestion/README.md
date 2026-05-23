# services/ingestion

Market data, news, and filings ingestion. Pluggable per-source connectors.

**Owner:** Machine A (Cortex)
**Language:** Python 3.13

## Initial connectors (priority order)

1. **SEC EDGAR** — US filings (10-K, 10-Q, 8-K, 4). Free, rich, LLM-friendly.
2. **News RSS aggregate** — Yahoo Finance / Seeking Alpha / Bloomberg headline feeds
3. **Finnhub free tier** — earnings calendar, basic fundamentals
4. **Crypto** — CoinGecko free + a public on-chain RPC (later phase)

## Contract

Every connector implements:

```python
class Connector(Protocol):
    name: str
    async def discover(self, since: datetime) -> list[DocumentRef]: ...
    async def fetch(self, ref: DocumentRef) -> RawDocument: ...
```

Outputs land in `raw_documents` table; `services/graph` picks them up async.

## Setup (when scaffolded)

```bash
cd services/ingestion
uv sync
uv run python -m ingestion.main
```

# services/ingestion

Market data, news, and filings ingestion. Pluggable per-source connectors.

**Language:** Python 3.13
**Tier:** Phase 1

## Currently implemented

- **Bybit V5 public WebSocket** — testnet by default, streams `publicTrade.<SYMBOL>` for any USDT-perpetual

## Setup

```bash
# from repo root, first time only:
docker compose up -d
cd infra/db && uv sync && uv run alembic upgrade head

# then in this service:
cd ../../services/ingestion
uv sync

# run (defaults: BTCUSDT on testnet)
uv run python -m ingestion.main
# or with custom symbols
uv run python -m ingestion.main BTCUSDT ETHUSDT SOLUSDT
```

Press Ctrl+C to stop cleanly.

## Verifying it works

After running for ~30 seconds, check the DB:

```bash
docker compose exec postgres psql -U matrix -d matrix -c "
  SELECT exchange, symbol, COUNT(*), MIN(trade_ts), MAX(trade_ts)
  FROM market_trades
  GROUP BY exchange, symbol;"
```

You should see rows from `bybit-testnet` for the symbols you subscribed to.

## Architecture notes

- `connectors/bybit.py` — pure data emitter, no DB knowledge. Yields `TradePrint` dataclass instances over an async generator.
- `persist.py` — batches inserts (size or time triggered) and uses `ON CONFLICT DO NOTHING` so reconnect replays are idempotent.
- `main.py` — wires connector → persistence, handles SIGINT/SIGTERM for clean shutdown.

Future connectors (news RSS, SEC EDGAR, on-chain) follow the same pattern:
emit normalized dataclasses → a generic persistence layer handles batching + dedup.

## Roadmap

- [ ] Order-book snapshot stream (`orderbook.50.<symbol>`)
- [ ] Funding-rate + open-interest stream
- [ ] Multi-symbol fan-out (currently single WS connection per process; will become job-queue-driven)
- [ ] News RSS connector
- [ ] On-chain (Ethereum/Solana) tx connector

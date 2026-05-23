# services/backtest

Historical replay, paper-trade ledger, outcome scoring loop.

**Owner:** Machine A (Cortex)
**Language:** Python 3.13

## Responsibilities

- Replay strategies against historical data for backtesting new strategy hypotheses
- Maintain paper-trade ledger: simulated PnL with realistic slippage/fees
- Close out `predictions` against actual outcomes once horizon expires
- Feed scores back to `services/strategy` reflection loop

## Setup (when scaffolded)

```bash
cd services/backtest
uv sync
uv run python -m backtest.main
```

"""HTTP wrapper around `historical.run_backtest` so the dashboard (Next.js)
can show a backtest preview before an operator promotes a template.

This is a separate process from the paper-trade daemon — we spin it up
as `backtest-api` in docker-compose, listening on port 8010. Same image
as the daemon; just a different command. No database writes; this is a
read-and-replay path.

Endpoints:
    GET  /healthz                — liveness probe
    POST /backtest               — one-shot backtest, returns BacktestResult JSON

The POST body is a strict shape: { strategy, symbol, asset_class?, days?, params? }.
Same args as the CLI in historical.main. params may carry "price_band_pct"
as a decimal string — we coerce to Decimal before passing through.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from matrix_shared.markets.crypto import crypto_universe

from backtest.historical import run_backtest


class BacktestRequest(BaseModel):
    strategy: str = Field(..., examples=["grid"])
    # `symbol` is optional; when omitted on a crypto request the server fills
    # it with crypto_universe()[0] so the UI doesn't need a hardcoded fallback.
    # BIST callers must supply it explicitly (the BIST universe is DB-driven).
    symbol: str | None = Field(default=None, examples=[None])
    asset_class: str = "crypto"
    days: int = 7
    # Strategy-specific knobs (Grid: n_grids, price_band_pct, horizon_s)
    params: dict[str, Any] = Field(default_factory=dict)
    # When true, positions list is included verbatim. Default off so a 7-day
    # backtest doesn't ship hundreds of rows for a quick UI preview.
    include_positions: bool = False


def _coerce_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert JSON-friendly types to what `grid_replay` expects.

    HTTP clients send decimals as strings (or floats). Inside the engine
    everything is Decimal — match that so the math stays exact."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "price_band_pct":
            out[k] = Decimal(str(v))
        elif k in {"n_grids", "horizon_s"}:
            out[k] = int(v)
        else:
            out[k] = v
    return out


def build_app() -> FastAPI:
    app = FastAPI(title="matrix-backtest-api", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/backtest")
    async def backtest(req: BacktestRequest) -> dict[str, Any]:
        symbol = req.symbol
        if not symbol:
            if req.asset_class == "crypto":
                symbol = crypto_universe()[0]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"symbol is required for asset_class={req.asset_class}",
                )
        try:
            params = _coerce_params(req.params)
            result = await run_backtest(
                req.strategy, symbol, req.asset_class, req.days, params,
            )
        except ValueError as e:
            # Unknown strategy or bad arg — operator error, return 400 not 500.
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.exception(f"backtest failed: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e

        payload = asdict(result)
        if not req.include_positions:
            # The default — replace the heavy list with just its count so the
            # preview UI stays responsive.
            n = len(result.positions)
            payload["positions"] = {"omitted": True, "count": n}
        return payload

    return app


# Lazy attribute so uvicorn's "module:app" target keeps working without
# pre-instantiating at import time (FastAPI is cheap, but explicit is nicer).
app = build_app()


def main() -> None:
    """uvicorn entry. We keep it lean: single worker, sane log level."""
    import uvicorn

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info("backtest-api start: listening on 0.0.0.0:8010")
    uvicorn.run(
        "backtest.api:app",
        host="0.0.0.0",
        port=8010,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()

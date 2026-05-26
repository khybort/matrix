"""Live-execution daemon — Phase 0 idle skeleton, now market-aware.

This process exists for two reasons before Phase 5 broker code lands:

  1. Operational reassurance: a long-running banner saying "execution
     layer is up, gate is closed" makes the wall visible. If it isn't
     in `docker compose ps`, someone might forget to wire it in later.

  2. A ready slot in the topology. When the broker connector arrives,
     it plugs into this process — same env, same restart policy, same
     log stream — instead of provisioning a new service from scratch.

Phase D change (market parity): the daemon no longer hard-codes Bybit.
It walks `matrix_shared.markets.all_markets()` and reports per-market
adapter status, so BIST shows up as "not wired (Phase 1)" right next
to crypto's "ready, gate closed". No daemon code change is needed when
BIST live execution lands — only `BistLiveExecutor` needs filling in.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from dataclasses import dataclass

from loguru import logger

from matrix_shared.markets import ExecutionAdapter, all_markets

# Side-effect import: ensures CryptoLiveExecutor / BistLiveExecutor are
# importable when matrix_shared.markets factories late-bind to them.
import execution.adapters  # noqa: F401
from execution.safety import _env_live_enabled

DEFAULT_HEARTBEAT_S = 60.0


@dataclass(slots=True)
class _ExecCfg:
    testnet: bool


def _executor_cfg() -> _ExecCfg:
    testnet = os.environ.get("BYBIT_TESTNET", "true").strip().lower() != "false"
    return _ExecCfg(testnet=testnet)


async def _report_market(
    market_name: str,
    executor: ExecutionAdapter | None,
    wired_error: str | None,
) -> None:
    if wired_error is not None:
        logger.info(f"[{market_name}] adapter not wired: {wired_error}")
        return
    assert executor is not None
    try:
        healthy = await executor.health()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{market_name}] health check raised: {e}")
        healthy = False
    logger.info(
        f"[{market_name}] adapter={type(executor).__name__} healthy={healthy}"
    )


async def _heartbeat_once(cfg: _ExecCfg) -> None:
    live = _env_live_enabled()
    cap = os.environ.get("LIVE_CAPITAL_CAP_USD", "(unset)")
    if live:
        logger.warning(
            f"LIVE_EXECUTION_ENABLED=true; LIVE_CAPITAL_CAP_USD={cap}. "
            "Order submission still requires per-strategy cert + gate green."
        )
    else:
        logger.info(
            "gate CLOSED (LIVE_EXECUTION_ENABLED=false). "
            "Ready and idle; no orders will be submitted."
        )

    for market in all_markets():
        executor: ExecutionAdapter | None = None
        wired_error: str | None = None
        try:
            executor = market.make_executor(cfg, paper=False)
        except (NotImplementedError, RuntimeError) as e:
            wired_error = str(e)
        await _report_market(market.name, executor, wired_error)


async def run(heartbeat_s: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    cfg = _executor_cfg()
    while not stop.is_set():
        try:
            await _heartbeat_once(cfg)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"heartbeat raised: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=heartbeat_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Matrix live-execution daemon (Phase 0 skeleton, market-aware)"
    )
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=DEFAULT_HEARTBEAT_S,
        help=f"Heartbeat log interval seconds (default {DEFAULT_HEARTBEAT_S})",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:HH:mm:ss} | {level: <5} | {message}",
    )
    logger.info(
        "execution daemon start "
        f"(markets={[m.name for m in all_markets()]}, gate enforced by safety.py)"
    )

    asyncio.run(run(args.heartbeat))


if __name__ == "__main__":
    main()

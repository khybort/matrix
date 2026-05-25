"""Live-execution daemon — Phase 0 idle skeleton.

This process exists for two reasons before Phase 5 broker code lands:

  1. Operational reassurance: a long-running banner saying "execution
     layer is up, gate is closed" makes the wall visible. If it isn't
     in `docker compose ps`, someone might forget to wire it in later.

  2. A ready slot in the topology. When the broker connector arrives,
     it plugs into this process — same env, same restart policy, same
     log stream — instead of provisioning a new service from scratch.

The daemon does NOT poll for predictions or open positions. That's the
broker connector's job (Phase 5). All we do here is loop, log status,
and stand ready.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from loguru import logger

from execution.safety import _env_live_enabled

DEFAULT_HEARTBEAT_S = 60.0


async def run(heartbeat_s: float) -> None:
    stop = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        live = _env_live_enabled()
        cap = os.environ.get("LIVE_CAPITAL_CAP_USD", "(unset)")
        # Banner makes a misconfiguration obvious in `docker logs`.
        if live:
            logger.warning(
                f"LIVE_EXECUTION_ENABLED=true; LIVE_CAPITAL_CAP_USD={cap}. "
                "Broker connector is NOT wired yet — no orders will leave this process."
            )
        else:
            logger.info(
                f"gate CLOSED (LIVE_EXECUTION_ENABLED=false). "
                "Ready and idle; no orders will be submitted."
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=heartbeat_s)
        except TimeoutError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Matrix live-execution daemon (Phase 0 skeleton)")
    parser.add_argument(
        "--heartbeat", type=float, default=DEFAULT_HEARTBEAT_S,
        help=f"Heartbeat log interval seconds (default {DEFAULT_HEARTBEAT_S})",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level: <5} | {message}")
    logger.info(
        "execution daemon start (Phase 0 skeleton — no broker connector)"
    )

    asyncio.run(run(args.heartbeat))


if __name__ == "__main__":
    main()

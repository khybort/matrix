#!/usr/bin/env python3
"""One-shot Bybit testnet market order (smoke test)."""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal

from matrix_shared.bybit_v5 import BybitOrder, BybitV5Client


async def main() -> None:
    symbol = os.environ.get("BYBIT_TEST_SYMBOL", "BTCUSDT")
    qty = Decimal(os.environ.get("BYBIT_TEST_QTY", "0.001"))
    testnet = os.environ.get("BYBIT_TESTNET", "true").strip().lower() != "false"

    client = BybitV5Client(testnet=testnet)
    try:
        result = await client.place_order(
            BybitOrder(
                category="linear",
                symbol=symbol,
                side="Buy",
                order_type="Market",
                qty=qty,
            )
        )
        print("dry_run:", result.dry_run)
        print("ok:", result.ok)
        print("order_id:", result.order_id)
        print("retCode:", result.raw.get("retCode"), result.raw.get("retMsg"))
        if not result.ok:
            sys.exit(1)
        hist = await client._signed_get(
            f"/v5/order/history?category=linear&symbol={symbol}&limit=5"
        )
        print("recent orders:", len((hist.get("result") or {}).get("list") or []))
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python
"""Print the volatility-scaled barrier study (matrix_shared.barrier_study).

    make barrier-report [DAYS=14] [STRATEGY=grid]
"""
from __future__ import annotations

import argparse
import asyncio
import json

from matrix_shared.barrier_study import DEFAULT_GRID, format_report, run_barrier_study


def main() -> None:
    ap = argparse.ArgumentParser(description="Volatility-scaled barriers vs fixed ones")
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = asyncio.run(run_barrier_study(days=a.days, strategy_id=a.strategy))
    print(json.dumps(rows, indent=2) if a.json else format_report(rows, days=a.days, grid=DEFAULT_GRID))


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Print the volatility-scaled barrier study (matrix_shared.barrier_study).

    make barrier-report [DAYS=14] [STRATEGY=grid]
"""
from __future__ import annotations

import argparse
import asyncio
import json

from matrix_shared.barrier_study import (
    DEFAULT_GRID,
    format_horizon_report,
    format_report,
    run_barrier_study,
    run_horizon_study,
)
from sample_units import units_table


def main() -> None:
    ap = argparse.ArgumentParser(description="Volatility-scaled barriers vs fixed ones")
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--horizon", action="store_true", help="alpha-decay profile instead of barriers")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.horizon:
        rows = asyncio.run(run_horizon_study(days=a.days, strategy_id=a.strategy))
        print(json.dumps(rows, indent=2) if a.json
              else format_horizon_report(rows, days=a.days) + units_table(rows))
        return
    rows = asyncio.run(run_barrier_study(days=a.days, strategy_id=a.strategy))
    print(json.dumps(rows, indent=2) if a.json
          else format_report(rows, days=a.days, grid=DEFAULT_GRID) + units_table(rows))


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Post-only entry vs crossing (matrix_shared.execution_study).

    make execution-report [DAYS=14] [STRATEGY=momentum_xs] [WAIT=1]
"""
from __future__ import annotations

import argparse
import asyncio
import json

from matrix_shared.execution_study import WAIT_BARS, format_report, run_execution_study


def main() -> None:
    ap = argparse.ArgumentParser(description="Post-only entry study")
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--wait-bars", type=int, default=WAIT_BARS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = asyncio.run(run_execution_study(days=a.days, strategy_id=a.strategy, wait_bars=a.wait_bars))
    print(json.dumps(rows, indent=2) if a.json
          else format_report(rows, days=a.days, wait_bars=a.wait_bars))


if __name__ == "__main__":
    main()

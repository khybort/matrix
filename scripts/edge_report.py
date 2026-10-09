#!/usr/bin/env python
"""Print the entry-timing edge report (see matrix_shared.edge_study).

    make edge-report [DAYS=14] [STRATEGY=grid] [DRAWS=20]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from matrix_shared.edge_study import format_report, run_edge_study
from matrix_shared.trading import execution_cost_bps
from sample_units import units_table


def main() -> None:
    ap = argparse.ArgumentParser(description="Entry-timing edge vs random entry")
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--draws", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = asyncio.run(run_edge_study(days=a.days, strategy_id=a.strategy, draws=a.draws))
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    cost = float(execution_cost_bps("crypto")) * 2
    print(format_report(rows, days=a.days, cost_bps=cost), file=sys.stdout)
    print(units_table(rows))


if __name__ == "__main__":
    main()

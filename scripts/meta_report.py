#!/usr/bin/env python
"""Meta-labeling study: should we act on this signal? (matrix_shared.meta_label)

    make meta-report [DAYS=14] [STRATEGY=momentum_xs]
"""
from __future__ import annotations

import argparse
import asyncio
import json

from matrix_shared.meta_label import format_report, run_meta_study


def main() -> None:
    ap = argparse.ArgumentParser(description="Meta-label (act / skip) study")
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = asyncio.run(run_meta_study(days=a.days, strategy_id=a.strategy))
    print(json.dumps(rows, indent=2) if a.json else format_report(rows, days=a.days))


if __name__ == "__main__":
    main()

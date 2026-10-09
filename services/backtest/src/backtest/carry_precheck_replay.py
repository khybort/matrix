"""Replay the executor's pre-trade checks over past book-priced carry entries.

`carry_executor.paper_open_precheck` as of each position's open (or the
signal's birth when it never opened), on the books and borrow quotes recorded
then: would the live executor have entered? `--tag` writes
`context.exec_precheck = would_abort` on positions that fail, so the shadow
tracker and the carry evidence drop them; nothing is closed. `--now` adds the
same check at the current time, for contrast (a quote that moved after entry
says nothing about the entry).

    docker compose exec backtest uv run python -m backtest.carry_precheck_replay \
        --strategy neg_funding_carry --since 2026-10-09T13:30:00+00:00 [--tag] [--now]
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from matrix_shared import shared_session_scope
from matrix_shared.carry_executor import PRECHECK_WOULD_ABORT, paper_open_precheck
from matrix_shared.models import PaperPosition, Prediction

from backtest.paper_trade import set_exec_precheck


async def replay(strategy_id: str, since: datetime, *, tag: bool = False, now: bool = False) -> list[dict]:
    async with shared_session_scope() as s:
        rows = (await s.execute(
            select(Prediction, PaperPosition)
            .outerjoin(PaperPosition, PaperPosition.prediction_id == Prediction.id)
            .where(Prediction.strategy_id == strategy_id, Prediction.generated_at >= since)
            .order_by(Prediction.generated_at)
        )).all()
    out = []
    for pred, pos in rows:
        ctx = pred.context or {}
        if not ctx.get("spot_venue") or not ctx.get("spot_symbol"):
            continue  # not book-priced: the executor never sees it
        leg = pos.notional_usd if pos is not None else Decimal(str((ctx.get("book_open") or {}).get("notional_usd") or 100))
        at = pos.opened_at if pos is not None else pred.generated_at
        at = at if at.tzinfo else at.replace(tzinfo=UTC)
        pc = await paper_open_precheck(pred, wallet_id=pos.wallet_id if pos else pred.id, leg_usd=leg, at=at)
        row = {"id": str(pred.id), "symbol": pred.symbol, "at": at.isoformat(), "opened": pos is not None,
               "ok": pc.ok, "reason": pc.reason, "checks": pc.checks, "detail": pc.detail}
        if now:
            pn = await paper_open_precheck(pred, wallet_id=pred.id, leg_usd=leg)
            row["now"] = {"ok": pn.ok, "reason": pn.reason, "checks": pn.checks, "detail": pn.detail}
        if tag and pos is not None and not pc.ok:
            await set_exec_precheck(pred.id, pc.record(PRECHECK_WOULD_ABORT, source="replay", at=at.isoformat()))
            row["tagged"] = True
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="neg_funding_carry")
    ap.add_argument("--since", required=True)
    ap.add_argument("--tag", action="store_true")
    ap.add_argument("--now", action="store_true")
    a = ap.parse_args()
    rows = asyncio.run(replay(a.strategy, datetime.fromisoformat(a.since), tag=a.tag, now=a.now))
    for r in rows:
        print(f"{r['symbol']:<12} at {r['at'][:19]} opened={r['opened']} "
              f"{'PASS' if r['ok'] else 'ABORT: ' + str(r['reason'])}{' [tagged would_abort]' if r.get('tagged') else ''}")
        for k, v in r["checks"].items():
            print(f"    {k:<13} {v:<8} {r['detail'].get(k, '')}")
        if "now" in r:
            n = r["now"]
            print(f"    now: {'PASS' if n['ok'] else 'ABORT: ' + str(n['reason'])}  "
                  + "; ".join(f"{k}={v}" for k, v in n["checks"].items()))
    c = Counter("pass" if r["ok"] else next(k for k, v in r["checks"].items() if v == "fail") for r in rows)
    print(f"\n{len(rows)} book-priced entries: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))


if __name__ == "__main__":
    main()

"""ingestion.iv_term_recorder: round 5's paging of the Deribit trade window and
the missing-day schedule (today first, then backfill, holes refilled)."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import httpx

from ingestion import iv_term_recorder as R

DAY_MS = 1_791_504_000_000  # 2026-10-09 00:00 UTC


def _trade(i, ts):
    return {"trade_id": f"t{i}", "timestamp": ts, "instrument_name": "BTC-16OCT26-100000-C",
            "iv": 50.0, "index_price": 100_000.0, "amount": 1.0}


def test_fetch_window_pages_and_dedups_same_ms_trades(monkeypatch):
    monkeypatch.setattr(R, "PAUSE_S", 0)
    t0 = DAY_MS - 4 * 3_600_000
    # page 1 ends on a millisecond shared with page 2's first trade: re-served, deduped by id
    pages = [
        {"trades": [_trade(1, t0), _trade(2, t0 + 5)], "has_more": True},
        {"trades": [_trade(2, t0 + 5), _trade(3, t0 + 9)], "has_more": False},
    ]
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(req.url)).query)
        seen.append((int(q["start_timestamp"][0]), int(q["end_timestamp"][0])))
        return httpx.Response(200, json={"result": pages[len(seen) - 1]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await R.fetch_window(c, "BTC", DAY_MS)

    trades = asyncio.run(go())
    assert sorted(t["trade_id"] for t in trades) == ["t1", "t2", "t3"]
    assert seen == [(t0, DAY_MS - 1), (t0 + 5, DAY_MS - 1)]


def test_missing_days_newest_first_and_holes(monkeypatch):
    monkeypatch.setattr(R, "BACKFILL_DAYS", 3)
    today = date(2026, 10, 9)
    have = {("BTC", today), ("ETH", today), ("BTC", today - timedelta(days=2))}
    todo = R.missing_days(have, today)
    assert todo == [
        ("BTC", today - timedelta(days=1)), ("ETH", today - timedelta(days=1)),
        ("ETH", today - timedelta(days=2)),
        ("BTC", today - timedelta(days=3)), ("ETH", today - timedelta(days=3)),
    ]

"""Paper and the executor make the same open decision on the same inputs.

Both call `carry_executor.precheck_open` (books, borrow-quote drift, borrow
quota, margin): paper through `paper_open_precheck`, the executor inside
`open`. Each case feeds both the same books and recorded quote and checks
they agree: pass/abort, the abort reason, and every check's status.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

import matrix_shared.carry_executor as CE
from matrix_shared.carry_executor import Book, BorrowQuote, CarryExecutor, CarryIntent, InstrumentSpec, MemoryStore


def _ladder(mid: float, side: int, n: int = 40, qty: float = 50.0, tick: float = 0.0005) -> list:
    return [(round(mid + side * (i + 1) * tick, 6), qty) for i in range(n)]


DEEP = Book(bids=_ladder(1.0, -1), asks=_ladder(1.0, +1), source="test")
SPEC = InstrumentSpec(qty_step=Decimal("1"), min_qty=Decimal("1"), tick=Decimal("0.0001"), min_notional=Decimal("5"))
LEG = Decimal("100")  # = the test wallet's cap ($10k x 2 % / 2): executor qty 100 = paper qty
KAIA_PRICED, KAIA_NOW = Decimal("0.00007503833333333333333333333333"), Decimal("0.0001469958333333333333333333333")


def q(hourly: str | Decimal, max_borrow: str | None = "1000000", borrowable: bool = True) -> BorrowQuote:
    return BorrowQuote(hourly=Decimal(str(hourly)), max_borrow=Decimal(max_borrow) if max_borrow else None,
                       borrowable=borrowable)


# (id, perp book, spot book, recorded quote, priced quote, expected first failing check or None)
CASES = [
    ("all_pass", DEEP, DEEP, q("0.00005"), "0.00005", None),
    ("no_perp_book", None, DEEP, q("0.00005"), "0.00005", "books"),
    ("no_spot_book", DEEP, None, q("0.00005"), "0.00005", "books"),
    ("drift_at_limit", DEEP, DEEP, q("0.000075"), "0.00005", None),
    ("drift_over_limit", DEEP, DEEP, q("0.0000751"), "0.00005", "borrow_drift"),
    ("kaia_now", DEEP, DEEP, q(KAIA_NOW), KAIA_PRICED, "borrow_drift"),
    ("quote_fell", DEEP, DEEP, q("0.00001"), "0.00005", None),
    ("no_recorded_quote", DEEP, DEEP, None, "0.00005", None),
    ("signal_priced_none", DEEP, DEEP, q("0.00005"), None, None),
    ("not_borrowable", DEEP, DEEP, q("0.00005", borrowable=False), "0.00005", "borrow_quota"),
    ("quota_below_qty", DEEP, DEEP, q("0.00005", max_borrow="50"), "0.00005", "borrow_quota"),
    ("quota_at_qty", DEEP, DEEP, q("0.00005", max_borrow="100"), "0.00005", None),
    ("quota_unrecorded", DEEP, DEEP, q("0.00005", max_borrow=None), "0.00005", None),
]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
async def test_paper_and_executor_decide_alike(case, wallet_id, monkeypatch):
    _id, perp, spot, quote, priced, fails = case

    async def books(venue, category, symbol, at=None):
        return perp if category == "linear" else spot

    async def borrow_quote(venue, coin, at=None):
        return quote

    async def specs(venue, category, symbol):
        return SPEC

    monkeypatch.setattr(CE, "db_book", books)
    monkeypatch.setattr(CE, "db_borrow_quote", borrow_quote)
    ctx = {"spot_venue": "bybit", "spot_symbol": "TSTUSDT"}
    if priced:
        ctx["borrow_rate_hourly"] = str(priced)
    pred = SimpleNamespace(id=uuid.uuid4(), strategy_id="test_nfc", strategy_version=1, asset_class="crypto",
                           symbol="TSTUSDT", context=ctx)

    paper = await CE.paper_open_precheck(pred, wallet_id=wallet_id, leg_usd=LEG)

    ex = CarryExecutor(mode="dry_run", books=books, specs=specs, borrow_quote=borrow_quote, store=MemoryStore())
    it = CarryIntent(prediction_id=pred.id, strategy_id="test_nfc", strategy_version=1, wallet_id=wallet_id,
                     perp_symbol="TSTUSDT", spot_venue="bybit", spot_symbol="TSTUSDT", leg_usd=LEG,
                     borrow_hourly=Decimal(str(priced)) if priced else None)
    res = await ex.open(it)

    assert paper.ok is (fails is None)
    assert (res.status != "aborted") is paper.ok, (res.status, res.reasons)
    assert res.precheck["checks"] == paper.checks
    assert res.precheck["failed"] == fails
    if fails:
        assert res.reasons[-1] == paper.reason
        assert paper.checks[fails] == CE.FAIL
    else:
        assert res.status == "done", res.reasons

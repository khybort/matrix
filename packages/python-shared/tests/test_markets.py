"""Unit tests for the MarketAdapter registry and per-market rules.

DB-free: covers `claims_symbol`, `is_session_open`, fees/short/settlement,
and the registry helpers. Async universe/latest_price are exercised in
service-level integration tests.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from matrix_shared.markets import (
    MarketAdapter,
    all_markets,
    get_market,
    infer_market,
)
from matrix_shared.markets.bist import BistMarket
from matrix_shared.markets.crypto import CryptoMarket

TR = ZoneInfo("Europe/Istanbul")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_registry_has_crypto_and_bist() -> None:
    names = {m.name for m in all_markets()}
    assert names == {"crypto", "bist"}


def test_get_market_returns_singleton() -> None:
    a = get_market("crypto")
    b = get_market("crypto")
    assert a is b
    assert isinstance(a, CryptoMarket)


def test_get_market_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_market("forex")


def test_all_markets_implement_adapter() -> None:
    for m in all_markets():
        assert isinstance(m, MarketAdapter)
        assert isinstance(m.name, str) and m.name
        assert isinstance(m.asset_class, str) and m.asset_class


# ---------------------------------------------------------------------------
# Symbol routing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "symbol",
    ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDC", "FOOFDUSD"],
)
def test_infer_crypto_symbols(symbol: str) -> None:
    assert infer_market(symbol).name == "crypto"


@pytest.mark.parametrize(
    "symbol",
    ["THYAO", "GARAN", "AKBNK", "ASELS", "TOASO", "PETKM"],
)
def test_infer_bist_symbols(symbol: str) -> None:
    # Cache off → relies on regex fallback.
    BistMarket._known_symbols = None
    assert infer_market(symbol).name == "bist"


def test_no_market_claims_unknown() -> None:
    BistMarket._known_symbols = None
    with pytest.raises(ValueError, match="no market claims"):
        infer_market("AB")  # too short for BIST regex, no crypto suffix


def test_bist_cache_overrides_regex() -> None:
    # When the cache is primed, only listed symbols are claimed.
    BistMarket.set_known_symbols({"THYAO"})
    assert get_market("bist").claims_symbol("THYAO")
    assert not get_market("bist").claims_symbol("GARAN")
    # Reset for downstream tests.
    BistMarket._known_symbols = None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
def test_crypto_session_always_open() -> None:
    # 03:00 UTC on a Saturday — crypto doesn't care.
    sat_dawn = datetime(2026, 1, 3, 3, 0, 0, tzinfo=ZoneInfo("UTC"))
    assert get_market("crypto").is_session_open(sat_dawn)


def test_bist_session_weekend_closed() -> None:
    sat_noon = datetime(2026, 1, 3, 12, 0, 0, tzinfo=TR)
    assert get_market("bist").is_session_open(sat_noon) is False


def test_bist_session_weekday_open() -> None:
    weekday_noon = datetime(2026, 1, 5, 12, 0, 0, tzinfo=TR)  # Monday
    assert get_market("bist").is_session_open(weekday_noon) is True


def test_bist_session_before_open() -> None:
    weekday_early = datetime(2026, 1, 5, 8, 0, 0, tzinfo=TR)
    assert get_market("bist").is_session_open(weekday_early) is False


def test_bist_session_after_close() -> None:
    weekday_late = datetime(2026, 1, 5, 19, 0, 0, tzinfo=TR)
    assert get_market("bist").is_session_open(weekday_late) is False


def test_bist_naive_datetime_treated_as_utc() -> None:
    # 12:00 UTC on Monday = 15:00 TR — inside the session window.
    naive_utc = datetime(2026, 1, 5, 12, 0, 0)
    assert get_market("bist").is_session_open(naive_utc) is True


# ---------------------------------------------------------------------------
# Trading rules
# ---------------------------------------------------------------------------
def test_crypto_allows_short_true() -> None:
    assert get_market("crypto").allows_short() is True


def test_bist_allows_short_false() -> None:
    assert get_market("bist").allows_short() is False


def test_settlement_days() -> None:
    assert get_market("crypto").settlement_days() == 0
    assert get_market("bist").settlement_days() == 2


def test_crypto_fees_shape() -> None:
    f = get_market("crypto").fees("BTCUSDT")
    assert f.taker_bps > f.maker_bps
    assert f.slippage_bps >= Decimal("0")


def test_bist_fees_higher_than_crypto() -> None:
    crypto_f = get_market("crypto").fees("BTCUSDT")
    bist_f = get_market("bist").fees("THYAO")
    assert bist_f.taker_bps > crypto_f.taker_bps


# ---------------------------------------------------------------------------
# Factories not wired in Phase A
# ---------------------------------------------------------------------------
def test_make_ingestor_not_yet_wired() -> None:
    for m in all_markets():
        with pytest.raises(NotImplementedError):
            m.make_ingestor({})


def test_make_executor_not_yet_wired() -> None:
    for m in all_markets():
        with pytest.raises(NotImplementedError):
            m.make_executor({}, paper=True)

from decimal import Decimal

from matrix_shared.exchange_shadow import _qty_from_notional, shadow_enabled


def test_qty_from_notional_floors_to_step(monkeypatch):
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    assert _qty_from_notional(Decimal("100"), Decimal("50000")) == Decimal("0.002")


def test_shadow_enabled_requires_live(monkeypatch):
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("MATRIX_EXCHANGE_SHADOW", "true")
    assert shadow_enabled() is False
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    assert shadow_enabled() is True
    monkeypatch.setenv("MATRIX_EXCHANGE_SHADOW", "false")
    assert shadow_enabled() is False

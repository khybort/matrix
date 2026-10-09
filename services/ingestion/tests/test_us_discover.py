"""Tests for ingestion.us.discover (pure parsing/merge helpers)."""

from __future__ import annotations

from ingestion.us.discover import (
    DiscoveredSymbol,
    _merge,
    _normalize_ticker,
    _parse_sp500,
)


def test_normalize_ticker() -> None:
    assert _normalize_ticker("aapl") == "AAPL"
    assert _normalize_ticker("BRK.B") == "BRK-B"  # class-share dot → dash
    assert _normalize_ticker(" MSFT ") == "MSFT"
    assert _normalize_ticker("TOOLONGSYM") is None
    assert _normalize_ticker("123") is None


def test_merge_unions_index_membership() -> None:
    a = DiscoveredSymbol(symbol="AAPL", name="Apple", index_membership={"SP500"})
    b = DiscoveredSymbol(symbol="AAPL", name=None, sector="Tech", index_membership={"NDX"})
    c = DiscoveredSymbol(symbol="MSFT", index_membership={"SP500"})
    out = _merge([a], [b, c])
    by = {d.symbol: d for d in out}
    assert by["AAPL"].index_membership == {"SP500", "NDX"}
    assert by["AAPL"].name == "Apple"
    assert by["AAPL"].sector == "Tech"
    assert by["MSFT"].index_membership == {"SP500"}


def test_parse_sp500_minimal_table() -> None:
    html = """
    <table>
      <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>
      <tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td></tr>
      <tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td></tr>
    </table>
    """
    out = _parse_sp500(html)
    by = {d.symbol: d for d in out}
    assert "AAPL" in by and "BRK-B" in by
    assert by["AAPL"].name == "Apple Inc."
    assert by["AAPL"].sector == "Information Technology"
    assert by["AAPL"].index_membership == {"SP500"}

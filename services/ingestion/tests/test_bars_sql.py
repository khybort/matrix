"""The per-tick aggregation must constrain `symbol` so the (symbol, trade_ts)
index is used; without it every tick walked the whole 284M-row index."""

from ingestion.bars import _aggregate_sql


def test_symbol_filtered_sql_uses_any_array():
    filtered = str(_aggregate_sql(True))
    assert "symbol = ANY(:symbols)" in filtered
    assert "symbol = ANY" not in str(_aggregate_sql(False))
    for col in ("trade_ts >= :since", "trade_ts <  :until", "ON CONFLICT (asset_class, symbol, interval, ts)"):
        assert col in filtered

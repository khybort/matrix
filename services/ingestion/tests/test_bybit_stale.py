from ingestion.connectors.bybit import STALE_AFTER_S, is_stale


def test_stale_detection_threshold():
    assert not is_stale(100.0, 100.0 + STALE_AFTER_S, STALE_AFTER_S)
    assert is_stale(100.0, 100.0 + STALE_AFTER_S + 0.1, STALE_AFTER_S)

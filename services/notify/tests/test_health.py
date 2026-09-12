"""Pure tests for notify.health.detect_health_alerts — edge-triggered
liveness alerts derived from table freshness (no DB, no Telegram)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from notify.health import (
    ALERT_INFO,
    ALERT_URGENT,
    ALERT_WARNING,
    HealthFlags,
    HealthSample,
    detect_health_alerts,
)

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _healthy(**kw) -> HealthSample:
    base = dict(
        now=NOW,
        paper_snapshot_age_s=5.0,
        crypto_prediction_age_s=20.0,
        crypto_tick_age_s=2.0,
        crypto_bar_age_s=60.0,
        disk_free_pct=40.0,
        llm_configured=True,
        agent_predictions_in_window=20,
        agent_llm_predictions_in_window=7,
    )
    base.update(kw)
    return HealthSample(**base)


def test_healthy_sample_produces_no_alerts():
    alerts, flags = detect_health_alerts(HealthFlags(), _healthy())
    assert alerts == []
    assert flags.active == {}
    assert flags.last_dev_failed_check == NOW


def test_each_stall_condition_fires_with_expected_level():
    alerts, flags = detect_health_alerts(
        HealthFlags(),
        _healthy(
            paper_snapshot_age_s=400.0,
            crypto_prediction_age_s=2000.0,
            crypto_tick_age_s=900.0,
            crypto_bar_age_s=3000.0,
            disk_free_pct=5.0,
            agent_llm_predictions_in_window=0,
            dev_stuck_running=[7],
        ),
    )
    levels = {msg.split(":")[0][:2]: lvl for lvl, msg in alerts}
    kinds = set(flags.active)
    assert kinds == {
        "paper_engine_stalled", "signals_stalled", "ingestion_stalled",
        "bars_stalled", "disk_pressure", "llm_degraded", "dev_task_stuck",
    }
    by_level = {lvl for lvl, _ in alerts}
    assert ALERT_URGENT in by_level and ALERT_WARNING in by_level
    assert any("Paper engine stalled" in m for _, m in alerts)
    assert any("LLM path inactive" in m for _, m in alerts)


def test_condition_is_edge_triggered_then_realerts_after_interval():
    bad = _healthy(paper_snapshot_age_s=400.0)
    alerts, flags = detect_health_alerts(HealthFlags(), bad)
    assert len(alerts) == 1
    # Same condition one minute later → silent.
    later = _healthy(paper_snapshot_age_s=460.0, now=NOW + timedelta(minutes=1))
    alerts2, flags2 = detect_health_alerts(flags, later, realert_s=3600)
    assert alerts2 == []
    assert flags2.active["paper_engine_stalled"] == NOW
    # Past the re-alert interval → fires again.
    much_later = _healthy(paper_snapshot_age_s=9000.0, now=NOW + timedelta(hours=2))
    alerts3, flags3 = detect_health_alerts(flags2, much_later, realert_s=3600)
    assert len(alerts3) == 1
    assert flags3.active["paper_engine_stalled"] == NOW + timedelta(hours=2)


def test_recovery_emits_info_once():
    _, flags = detect_health_alerts(HealthFlags(), _healthy(crypto_tick_age_s=5000.0))
    alerts, flags2 = detect_health_alerts(flags, _healthy(now=NOW + timedelta(minutes=1)))
    assert alerts == [(ALERT_INFO, "ℹ️ Recovered: ingestion stalled.")]
    assert flags2.active == {}
    alerts3, _ = detect_health_alerts(flags2, _healthy(now=NOW + timedelta(minutes=2)))
    assert alerts3 == []


def test_llm_degraded_requires_configured_backend_and_enough_samples():
    a, _ = detect_health_alerts(
        HealthFlags(), _healthy(llm_configured=False, agent_llm_predictions_in_window=0)
    )
    assert a == []
    a, _ = detect_health_alerts(
        HealthFlags(), _healthy(agent_predictions_in_window=2, agent_llm_predictions_in_window=0)
    )
    assert a == []


def test_failed_dev_tasks_are_point_events():
    s = _healthy(dev_failed_since_prev=[(3, "max_turns"), (4, "")])
    alerts, flags = detect_health_alerts(HealthFlags(), s)
    assert [lvl for lvl, _ in alerts] == [ALERT_WARNING, ALERT_WARNING]
    assert "task #3 failed: max_turns" in alerts[0][1]
    assert "no reason recorded" in alerts[1][1]
    assert flags.active == {}
    # Unknown measurements never alert.
    alerts2, _ = detect_health_alerts(
        flags, HealthSample(now=NOW + timedelta(minutes=1), llm_configured=True)
    )
    assert alerts2 == []

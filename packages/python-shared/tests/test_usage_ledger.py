from datetime import UTC, datetime

from matrix_shared import usage_ledger as UL


def test_record_and_summary_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_USAGE_DIR", str(tmp_path))
    monkeypatch.setenv("MATRIX_SERVICE", "agent")
    UL.record(session="single_shot", backend="subscription", model="haiku", turns=1, cost_usd=0.02, is_error=False)
    UL.record(session="reflection", backend="subscription", model="sonnet", turns=7, cost_usd=0.03, is_error=True,
              reason="max_turns", duration_s=12.4)
    UL.record(session="single_shot", backend="subscription", model="haiku", turns=None, cost_usd=None, is_error=True,
              reason="timeout", duration_s=45.0)
    monkeypatch.setenv("MATRIX_SERVICE", "director")
    UL.record(session="director", backend="openrouter", model="x", turns=None, cost_usd=None, is_error=None)
    s = UL.summary(datetime.now(UTC))
    assert s["calls"] == 4 and s["cost_usd"] == 0.05
    agent = s["by_service"]["agent"]
    assert agent["calls"] == 3 and agent["turns"] == 8 and agent["cost_usd"] == 0.05
    assert agent["errors"] == 2 and agent["timeouts"] == 1
    assert agent["p50_s"] == 45.0 and agent["p95_s"] == 45.0
    assert s["by_service"]["director"]["calls"] == 1
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1 and files[0].name == f"{datetime.now(UTC):%Y-%m-%d}.jsonl"


def test_summary_is_empty_without_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_USAGE_DIR", str(tmp_path / "none"))
    assert UL.summary() == {"days": 1, "calls": 0, "cost_usd": 0.0, "by_service": {}}


def test_service_name_from_compose_workdir(monkeypatch, tmp_path):
    monkeypatch.delenv("MATRIX_SERVICE", raising=False)
    d = tmp_path / "services" / "reflection"
    d.mkdir(parents=True)
    monkeypatch.chdir(d)
    assert UL.service_name() == "reflection"

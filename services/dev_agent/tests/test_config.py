"""tests/test_config.py"""

from __future__ import annotations

import os

import pytest

from dev_agent.config import FORBIDDEN_PATHS, Config, load_config


def test_forbidden_paths_are_hardcoded():
    """These paths must be a tuple of strings, not loaded from env or config file."""
    assert isinstance(FORBIDDEN_PATHS, tuple)
    assert "services/strategy/" in FORBIDDEN_PATHS
    assert "services/agent/" in FORBIDDEN_PATHS
    assert "services/execution/" in FORBIDDEN_PATHS
    # Bypass guard: should be a tuple (immutable), not a list
    with pytest.raises((TypeError, AttributeError)):
        FORBIDDEN_PATHS.append("foo")  # type: ignore[attr-defined]


def test_load_config_from_env(monkeypatch):
    monkeypatch.setenv("LOCAL_DATABASE_URL", "postgres://x:y@db:5432/m")
    monkeypatch.setenv("DEV_AGENT_PARALLEL", "3")
    monkeypatch.setenv("DEV_AGENT_DAILY_COST_CAP_USD", "100")
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.parallel == 3
    assert cfg.daily_cost_cap_usd == 100.0
    assert cfg.database_url.startswith("postgres://")


def test_load_config_defaults(monkeypatch):
    monkeypatch.setenv("LOCAL_DATABASE_URL", "postgres://x:y@db:5432/m")
    monkeypatch.delenv("DEV_AGENT_PARALLEL", raising=False)
    monkeypatch.delenv("DEV_AGENT_DAILY_COST_CAP_USD", raising=False)
    cfg = load_config()
    assert cfg.parallel == 2
    assert cfg.daily_cost_cap_usd == 50.0

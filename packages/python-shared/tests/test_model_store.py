"""Every measured model resolves to one shared directory.

The failure this guards against is not hypothetical: on 2026-09-20 the cost
model resolved to the shared volume while the edge cache and the
pre-registration registry resolved to a per-container path. Reflection could
not see the edge cache, so after each restart it read "no measurement" as "no
edge" and demoted the one strategy with a confirmed edge; and a registry whose
only guarantee is that a target is written once was being written once per
container.
"""

from __future__ import annotations

from pathlib import Path

from matrix_shared.model_store import model_dir, model_path


def test_override_wins(monkeypatch):
    monkeypatch.setenv("MATRIX_MODEL_DIR", "/tmp/models-x")
    assert model_dir() == Path("/tmp/models-x")
    assert model_path("a.json") == Path("/tmp/models-x/a.json")


def test_default_is_the_shared_claude_config_volume(monkeypatch):
    monkeypatch.delenv("MATRIX_MODEL_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/root/.claude")
    assert model_dir() == Path("/root/.claude/matrix_models")


def test_every_measured_model_shares_one_directory(monkeypatch):
    """The point of the module: no file may resolve anywhere else."""
    monkeypatch.setenv("MATRIX_MODEL_DIR", "/tmp/models-y")
    import importlib

    from matrix_shared import edge_study, promotion, symbol_costs

    for mod in (symbol_costs, promotion, edge_study):
        importlib.reload(mod)
    paths = [symbol_costs.COSTS_PATH, promotion.REGISTRY_PATH, edge_study._CACHE_PATH]
    assert {p.parent for p in paths} == {Path("/tmp/models-y")}, paths
    assert len({p.name for p in paths}) == 3, "the three models must not collide"

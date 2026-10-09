"""dev_agent.integrate — test → commit → merge, with the dirty-tree guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_agent import integrate as I
from dev_agent.worktree import WorktreeManager

pytestmark = pytest.mark.asyncio


def _run(cwd, *args):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "git", "init", "-b", "main")
    _run(repo, "git", "config", "user.email", "t@t")
    _run(repo, "git", "config", "user.name", "t")
    (repo / "README.md").write_text("hi\n")
    (repo / "services" / "foo").mkdir(parents=True)
    (repo / "services" / "foo" / "x.py").write_text("x = 1\n")
    _run(repo, "git", "add", ".")
    _run(repo, "git", "commit", "-q", "-m", "init")
    return repo


def _task(**kw):
    base = {"id": 7, "description": "tweak x\n\nmore", "run_tests": False,
            "base_branch": "main", "commit_message": None}
    base.update(kw)
    return base


def test_test_targets_maps_paths_to_projects():
    assert I.test_targets([
        "services/strategy/src/strategy/main.py", "services/strategy/tests/t.py",
        "packages/python-shared/src/matrix_shared/x.py", "docs/a.md", "Makefile",
    ]) == ["services/strategy", "packages/python-shared"]


async def test_no_changes_is_a_noop_merge(repo, tmp_path):
    wt = WorktreeManager(repo, tmp_path / "wt").create(task_id=7)
    res = await I.integrate(wt=wt, repo_root=repo, worktree_root=tmp_path / "wt",
                            task=_task(), review_mode="auto")
    assert res.status == "merged" and res.commit_sha is None
    assert "no file changes" in res.notes


async def test_change_is_committed_and_merged_into_main(repo, tmp_path):
    wt = WorktreeManager(repo, tmp_path / "wt").create(task_id=7)
    (wt.path / "services" / "foo" / "x.py").write_text("x = 2\n")
    res = await I.integrate(wt=wt, repo_root=repo, worktree_root=tmp_path / "wt",
                            task=_task(), review_mode="auto")
    assert res.status == "merged", res.notes
    assert res.changed_files == ["services/foo/x.py"]
    assert (repo / "services" / "foo" / "x.py").read_text() == "x = 2\n"
    log = subprocess.run(["git", "log", "--oneline", "-3"], cwd=repo, capture_output=True, text=True).stdout
    assert "dev_agent: tweak x" in log and "Merge dev-agent/task-7" in log
    assert not (tmp_path / "wt" / "task-7").exists()  # cleaned up after merge


async def test_dirty_overlap_in_repo_leaves_branch_for_review(repo, tmp_path):
    wt = WorktreeManager(repo, tmp_path / "wt").create(task_id=7)
    (wt.path / "services" / "foo" / "x.py").write_text("x = 2\n")
    (repo / "services" / "foo" / "x.py").write_text("x = 99  # human mid-edit\n")
    res = await I.integrate(wt=wt, repo_root=repo, worktree_root=tmp_path / "wt",
                            task=_task(), review_mode="auto")
    assert res.status == "awaiting_review" and res.commit_sha
    assert "uncommitted edits" in res.notes
    assert (repo / "services" / "foo" / "x.py").read_text().startswith("x = 99")


async def test_failing_tests_fail_the_task(repo, tmp_path, monkeypatch):
    wt = WorktreeManager(repo, tmp_path / "wt").create(task_id=7)
    (wt.path / "services" / "foo" / "x.py").write_text("x = 2\n")

    async def fake_run_tests(path, targets):
        return {"targets": targets, "passed": False, "failed_target": targets[0], "output": {}}

    monkeypatch.setattr(I, "run_tests", fake_run_tests)
    res = await I.integrate(wt=wt, repo_root=repo, worktree_root=tmp_path / "wt",
                            task=_task(run_tests=True), review_mode="auto")
    assert res.status == "failed" and res.failure_reason == "test_broke"
    # nothing merged, nothing committed
    assert (repo / "services" / "foo" / "x.py").read_text() == "x = 1\n"


async def test_manual_review_mode_commits_but_does_not_merge(repo, tmp_path):
    wt = WorktreeManager(repo, tmp_path / "wt").create(task_id=7)
    (wt.path / "README.md").write_text("changed\n")
    res = await I.integrate(wt=wt, repo_root=repo, worktree_root=tmp_path / "wt",
                            task=_task(), review_mode="manual")
    assert res.status == "awaiting_review" and res.commit_sha
    assert (repo / "README.md").read_text() == "hi\n"


async def test_branch_integration_mode_never_merges(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_AGENT_INTEGRATION", "branch")
    wt = WorktreeManager(repo, tmp_path / "wt").create(task_id=7)
    (wt.path / "README.md").write_text("changed\n")
    res = await I.integrate(wt=wt, repo_root=repo, worktree_root=tmp_path / "wt",
                            task=_task(), review_mode="auto")
    assert res.status == "awaiting_review" and "integration=branch" in res.notes


def test_test_env_never_points_uv_at_the_dev_agent_venv(monkeypatch):
    """uv run --project must build the worktree project's own .venv, not sync
    it into dev_agent's (UV_PROJECT_ENVIRONMENT is exported by the image)."""
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/workspace/services/dev_agent/.venv")
    monkeypatch.setenv("VIRTUAL_ENV", "/workspace/services/dev_agent/.venv")
    env = I._test_env()
    assert "UV_PROJECT_ENVIRONMENT" not in env and "VIRTUAL_ENV" not in env


def _pyproject(root, proj, *path_deps):
    d = root / proj
    d.mkdir(parents=True, exist_ok=True)
    srcs = "\n".join(f'dep{i} = {{ path = "{p}", editable = true }}' for i, p in enumerate(path_deps))
    (d / "pyproject.toml").write_text(f'[project]\nname = "x"\n\n[tool.uv.sources]\n{srcs}\n')


def test_a_shared_change_runs_every_consumer_suite(tmp_path):
    """A python-shared edit used to run only python-shared's own suite, though
    every service imports it; graph feeds agent/synthesis, and labs imports agent."""
    _pyproject(tmp_path, "packages/python-shared")
    _pyproject(tmp_path, "services/graph", "../../packages/python-shared")
    _pyproject(tmp_path, "services/agent", "../../packages/python-shared", "../graph")
    _pyproject(tmp_path, "services/labs", "../../packages/python-shared", "../agent")
    _pyproject(tmp_path, "services/strategy", "../../packages/python-shared")
    shared = ["packages/python-shared/src/matrix_shared/db.py"]
    assert I.test_targets(shared, tmp_path) == [
        "packages/python-shared", "services/agent", "services/graph", "services/labs", "services/strategy",
    ]
    assert I.test_targets(["services/graph/src/graph/x.py"], tmp_path) == [
        "services/graph", "services/agent", "services/labs",
    ]
    assert I.test_targets(["services/strategy/src/s.py"], tmp_path) == ["services/strategy"]


def _commit(repo, path, body):
    (repo / path).write_text(body)
    _run(repo, "git", "add", path)
    _run(repo, "git", "commit", "-q", "-m", f"seed {path}")


async def test_a_patch_that_drops_a_binding_is_rejected(repo, tmp_path):
    """A refactor deleted an import whose user no test reached (labs
    emit_signals, 2026-10-09): every suite passed, the service died with
    NameError. The names gate rejects it before tests or merge."""
    _commit(repo, "services/foo/y.py", "import os\n\n\ndef here():\n    return os.getcwd()\n")
    wt = WorktreeManager(repo, tmp_path / "wt").create(task_id=7)
    (wt.path / "services" / "foo" / "y.py").write_text("def here():\n    return os.getcwd()  # noqa: F821\n")
    res = await I.integrate(wt=wt, repo_root=repo, worktree_root=tmp_path / "wt",
                            task=_task(), review_mode="auto")
    assert res.status == "failed" and res.failure_reason == "undefined_name"
    assert "services/foo/y.py: F821 Undefined name `os`" in res.notes
    assert "import os" in (repo / "services" / "foo" / "y.py").read_text()  # nothing merged


async def test_a_duplicate_definition_is_rejected(repo, tmp_path):
    wt = WorktreeManager(repo, tmp_path / "wt").create(task_id=7)
    (wt.path / "services" / "foo" / "x.py").write_text(
        "def f():\n    return 1\n\n\ndef f():\n    return 2\n\n\nx = f()\n")
    res = await I.integrate(wt=wt, repo_root=repo, worktree_root=tmp_path / "wt",
                            task=_task(), review_mode="auto")
    assert res.failure_reason == "undefined_name" and "F811" in res.notes


async def test_a_name_error_already_on_main_does_not_block_an_unrelated_patch(repo, tmp_path):
    _commit(repo, "services/foo/y.py", "def here():\n    return missing\n")
    wt = WorktreeManager(repo, tmp_path / "wt").create(task_id=7)
    (wt.path / "services" / "foo" / "y.py").write_text("X = 1\n\n\ndef here():\n    return missing\n")
    res = await I.integrate(wt=wt, repo_root=repo, worktree_root=tmp_path / "wt",
                            task=_task(), review_mode="auto")
    assert res.status == "merged", res.notes

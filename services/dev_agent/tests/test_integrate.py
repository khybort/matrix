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

"""tests/test_worktree_mgr.py — uses a real git repo in a tmp dir."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_agent.worktree import WorktreeManager


def _run(cwd: Path, *args: str) -> str:
    r = subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)
    return r.stdout.strip()


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "git", "init", "-b", "main")
    _run(repo, "git", "config", "user.email", "t@t")
    _run(repo, "git", "config", "user.name", "t")
    (repo / "README.md").write_text("hello\n")
    _run(repo, "git", "add", ".")
    _run(repo, "git", "commit", "-m", "init")
    return repo


def test_create_worktree(fake_repo: Path, tmp_path: Path):
    wt_root = tmp_path / "worktrees"
    mgr = WorktreeManager(repo_root=fake_repo, worktree_root=wt_root)
    wt = mgr.create(task_id=42, base_branch="main")
    assert wt.path.exists()
    assert (wt.path / "README.md").exists()
    assert wt.branch == "dev-agent/task-42"
    branches = _run(fake_repo, "git", "branch", "--list", "dev-agent/task-42")
    assert "dev-agent/task-42" in branches


def test_create_is_idempotent_if_path_already_exists(fake_repo, tmp_path):
    wt_root = tmp_path / "worktrees"
    mgr = WorktreeManager(repo_root=fake_repo, worktree_root=wt_root)
    wt = mgr.create(task_id=1, base_branch="main")
    wt2 = mgr.create(task_id=1, base_branch="main")
    assert wt.path == wt2.path


def test_cleanup_removes_worktree_and_branch(fake_repo, tmp_path):
    wt_root = tmp_path / "worktrees"
    mgr = WorktreeManager(repo_root=fake_repo, worktree_root=wt_root)
    wt = mgr.create(task_id=7, base_branch="main")
    assert wt.path.exists()
    mgr.cleanup(task_id=7)
    assert not wt.path.exists()
    branches = _run(fake_repo, "git", "branch", "--list", "dev-agent/task-7")
    assert branches == ""


def test_status_returns_uncommitted_diff_summary(fake_repo, tmp_path):
    wt_root = tmp_path / "worktrees"
    mgr = WorktreeManager(repo_root=fake_repo, worktree_root=wt_root)
    wt = mgr.create(task_id=3, base_branch="main")
    (wt.path / "new.txt").write_text("data\n")
    s = mgr.status(task_id=3)
    assert s.dirty is True
    assert any("new.txt" in f for f in s.untracked)

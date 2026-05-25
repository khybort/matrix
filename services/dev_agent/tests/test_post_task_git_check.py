"""tests/test_post_task_git_check.py"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_agent.worktree import WorktreeManager, assert_no_unauthorized_commits


def _run(cwd, *args):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def repo_and_wt(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "git", "init", "-b", "main")
    _run(repo, "git", "config", "user.email", "t@t")
    _run(repo, "git", "config", "user.name", "t")
    (repo / "README.md").write_text("hi\n")
    _run(repo, "git", "add", ".")
    _run(repo, "git", "commit", "-m", "init")
    mgr = WorktreeManager(repo_root=repo, worktree_root=tmp_path / "wt")
    wt = mgr.create(task_id=1, base_branch="main")
    return repo, wt


def test_no_commits_after_no_op(repo_and_wt):
    _, wt = repo_and_wt
    assert_no_unauthorized_commits(wt, auto_commit=False)


def test_unauthorized_commit_is_reverted(repo_and_wt):
    repo, wt = repo_and_wt
    (wt.path / "stray.txt").write_text("oops\n")
    _run(wt.path, "git", "add", ".")
    _run(wt.path, "git", "commit", "-m", "agent committed without permission")
    assert_no_unauthorized_commits(wt, auto_commit=False)
    log = subprocess.run(
        ["git", "log", "--oneline", "main..HEAD"],
        cwd=str(wt.path), capture_output=True, text=True,
    )
    assert log.stdout.strip() == ""
    assert (wt.path / "stray.txt").exists()


def test_authorized_commit_passes_through(repo_and_wt):
    repo, wt = repo_and_wt
    (wt.path / "ok.txt").write_text("legit\n")
    _run(wt.path, "git", "add", ".")
    _run(wt.path, "git", "commit", "-m", "ok")
    assert_no_unauthorized_commits(wt, auto_commit=True)
    log = subprocess.run(
        ["git", "log", "--oneline", "main..HEAD"],
        cwd=str(wt.path), capture_output=True, text=True,
    )
    assert "ok" in log.stdout

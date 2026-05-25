"""Git worktree lifecycle for dev_agent tasks.

Each task gets a worktree at <worktree_root>/task-<id>/ on a branch
named dev-agent/task-<id>. Worktrees are NEVER auto-deleted; the operator
runs `make dev-agent-clean` to apply the cleanup policy.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


@dataclass
class Worktree:
    task_id: int
    path: Path
    branch: str


@dataclass
class WorktreeStatus:
    dirty: bool
    modified: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    staged: list[str] = field(default_factory=list)


class WorktreeManager:
    def __init__(self, repo_root: Path, worktree_root: Path) -> None:
        self.repo_root = Path(repo_root)
        self.worktree_root = Path(worktree_root)
        self.worktree_root.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: int, base_branch: str = "main") -> Worktree:
        path = self.worktree_root / f"task-{task_id}"
        branch = f"dev-agent/task-{task_id}"
        if path.exists():
            return Worktree(task_id=task_id, path=path, branch=branch)
        self._git("worktree", "add", "-b", branch, str(path), base_branch)
        return Worktree(task_id=task_id, path=path, branch=branch)

    def cleanup(self, task_id: int) -> None:
        path = self.worktree_root / f"task-{task_id}"
        branch = f"dev-agent/task-{task_id}"
        if path.exists():
            self._git("worktree", "remove", "--force", str(path))
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        try:
            self._git("branch", "-D", branch)
        except WorktreeError:
            pass

    def status(self, task_id: int) -> WorktreeStatus:
        path = self.worktree_root / f"task-{task_id}"
        if not path.exists():
            raise WorktreeError(f"worktree for task {task_id} does not exist")
        out = self._git_in(path, "status", "--porcelain=v1").splitlines()
        modified, untracked, staged = [], [], []
        for line in out:
            if not line:
                continue
            code = line[:2]
            filename = line[3:]
            if code.startswith("??"):
                untracked.append(filename)
            elif code[0] in ("M", "A", "D", "R"):
                staged.append(filename)
            if code[1] in ("M", "D"):
                modified.append(filename)
        return WorktreeStatus(
            dirty=bool(modified or untracked or staged),
            modified=modified,
            untracked=untracked,
            staged=staged,
        )

    def _git(self, *args: str) -> str:
        return self._git_in(self.repo_root, *args)

    def _git_in(self, cwd: Path, *args: str) -> str:
        r = subprocess.run(
            ("git", *args),
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise WorktreeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout.strip()


def assert_no_unauthorized_commits(wt: Worktree, *, auto_commit: bool) -> None:
    """Defense in depth. If auto_commit=False but the agent committed anyway,
    soft-reset back to the base branch — work is preserved in the working tree.
    """
    if auto_commit:
        return
    r = subprocess.run(
        ("git", "rev-list", "--count", "main..HEAD"),
        cwd=str(wt.path),
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return
    count = int((r.stdout or "0").strip())
    if count > 0:
        subprocess.run(
            ("git", "reset", "--soft", f"HEAD~{count}"),
            cwd=str(wt.path),
            check=True,
            capture_output=True,
        )

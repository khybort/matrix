"""Post-run integration: test → commit → merge the task branch into main.

Before 2026-09-12 a completed task was stamped `merged` in the DB while its
work stayed in an un-committed worktree (9/9 tasks, 0 commits on main). This
module makes the label true:

  1. Inspect the worktree. No changes → `noop` (task still counts as done).
  2. `run_tests`: run pytest for every touched service (`uv run --project`),
     plus python-shared when it changed. Any failure → task `failed`
     (`test_broke`) and the lesson synthesizer gets a real signal.
  3. Commit in the worktree (task description as message, `Task #<id>`).
  4. `DEV_AGENT_INTEGRATION=merge` (default): fast-forward-safe `git merge
     --no-ff` into `base_branch` in the live repo — but ONLY when the repo's
     working tree has no uncommitted edits touching the same files (a human
     or another session may be mid-change). Otherwise, or on conflict, the
     branch is left for review (`awaiting_review`, notes say why).
     `DEV_AGENT_INTEGRATION=branch`: never merge, always leave for review.
  5. Merged worktrees are removed (`DEV_AGENT_CLEANUP_AFTER_MERGE`).

Merging into the bind-mounted repo IS the deploy: watchfiles reloads the
touched services. Safety-critical files are protected upstream by
`FORBIDDEN_PATHS` (the agent cannot edit them at all).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from dev_agent.worktree import Worktree, WorktreeManager

TEST_TIMEOUT_S = int(os.environ.get("DEV_AGENT_TEST_TIMEOUT_S", "900"))


@dataclass
class IntegrationResult:
    status: str  # merged | awaiting_review | failed
    failure_reason: str | None = None
    notes: str = ""
    tests: dict[str, Any] | None = None
    commit_sha: str | None = None
    changed_files: list[str] = field(default_factory=list)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(("git", *args), cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r


def changed_files(cwd: Path) -> list[str]:
    out = _git(cwd, "status", "--porcelain=v1", "-uall").stdout
    files = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name = line[3:]
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        files.append(name)
    return files


def test_targets(files: list[str]) -> list[str]:
    """Project directories (relative) whose pytest suite must pass."""
    targets: list[str] = []
    for f in files:
        parts = Path(f).parts
        if len(parts) >= 2 and parts[0] == "services":
            t = f"services/{parts[1]}"
        elif len(parts) >= 2 and parts[0] == "packages" and parts[1] == "python-shared":
            t = "packages/python-shared"
        else:
            continue
        if t not in targets:
            targets.append(t)
    return targets


def _test_env() -> dict[str, str]:
    env = dict(os.environ)
    shared = env.get("SHARED_DATABASE_URL", "")
    local = env.get("LOCAL_DATABASE_URL", "")
    # Service conftests default to localhost DSNs; inside the container the
    # DB hosts are the compose service names.
    for key, val in (
        ("EXECUTION_TEST_SHARED_DSN", shared), ("REFLECTION_TEST_SHARED_DSN", shared),
        ("STRATEGY_TEST_LOCAL_DSN", local), ("DEV_AGENT_TEST_DSN", local),
    ):
        if val:
            env.setdefault(key, val)
    env.setdefault("MATRIX_LLM_BACKEND", "")
    return env


async def run_tests(wt_path: Path, targets: list[str]) -> dict[str, Any]:
    """Run each target's pytest via `uv run --project` inside the worktree."""
    results: dict[str, Any] = {"targets": targets, "passed": True, "output": {}}
    override = os.environ.get("DEV_AGENT_TEST_CMD", "").strip()
    for t in targets:
        proj = wt_path / t
        if not (proj / "tests").exists() or not (proj / "pyproject.toml").exists():
            results["output"][t] = "skipped: no tests/ or pyproject"
            continue
        cmd = shlex.split(override) if override else [
            "uv", "run", "--project", str(proj), "pytest", "-q", "-x", "-p", "no:cacheprovider",
            "-m", "not live", str(proj / "tests"),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(wt_path), env=_test_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=TEST_TIMEOUT_S)
            text = out.decode(errors="replace")
            rc = proc.returncode
        except asyncio.TimeoutError:
            proc.kill()
            text, rc = f"timeout after {TEST_TIMEOUT_S}s", 124
        tail = "\n".join(text.splitlines()[-40:])
        results["output"][t] = tail
        if rc != 0:
            results["passed"] = False
            results["failed_target"] = t
            break
    return results


def _dirty_overlap(repo_root: Path, files: list[str]) -> list[str]:
    dirty = set(changed_files(repo_root))
    return sorted(dirty & set(files))


def commit_worktree(wt: Worktree, message: str) -> str | None:
    _git(wt.path, "add", "-A")
    r = _git(wt.path, "-c", "user.name=matrix-dev-agent", "-c", "user.email=dev-agent@matrix.local",
             "commit", "-q", "-m", message, check=False)
    if r.returncode != 0:
        if "nothing to commit" in (r.stdout + r.stderr):
            return None
        raise RuntimeError(f"git commit: {r.stderr.strip()}")
    return _git(wt.path, "rev-parse", "HEAD").stdout.strip()


def merge_into_base(repo_root: Path, branch: str, base_branch: str, message: str) -> tuple[bool, str]:
    """Merge `branch` into `base_branch` in the live repo. Returns (ok, note)."""
    head = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if head != base_branch:
        return False, f"repo is on {head!r}, not {base_branch!r}; branch {branch} left for review"
    r = _git(repo_root, "merge", "--no-ff", "--no-edit", "-m", message, branch, check=False)
    if r.returncode != 0:
        _git(repo_root, "merge", "--abort", check=False)
        return False, f"merge conflict; branch {branch} left for review: {r.stderr.strip()[:300]}"
    return True, _git(repo_root, "rev-parse", "HEAD").stdout.strip()


async def integrate(
    *,
    wt: Worktree,
    repo_root: Path,
    worktree_root: Path,
    task: dict[str, Any],
    review_mode: str,
) -> IntegrationResult:
    files = changed_files(wt.path)
    if not files:
        return IntegrationResult(
            status="merged" if review_mode == "auto" else "awaiting_review",
            notes="no file changes produced",
        )

    tests: dict[str, Any] | None = None
    if task.get("run_tests", True):
        targets = test_targets(files)
        tests = await run_tests(wt.path, targets) if targets else {"targets": [], "passed": True}
        if not tests["passed"]:
            return IntegrationResult(
                status="failed", failure_reason="test_broke",
                notes=f"tests failed in {tests.get('failed_target')}", tests=tests,
                changed_files=files,
            )

    first_line = (task.get("description") or "dev_agent change").strip().splitlines()[0][:72]
    message = task.get("commit_message") or f"dev_agent: {first_line}\n\nTask #{task['id']}"
    sha = commit_worktree(wt, message)

    if review_mode != "auto":
        return IntegrationResult(status="awaiting_review", notes=f"committed {sha}; awaiting review",
                                 tests=tests, commit_sha=sha, changed_files=files)

    mode = os.environ.get("DEV_AGENT_INTEGRATION", "merge").strip().lower()
    if mode != "merge":
        return IntegrationResult(status="awaiting_review", notes=f"committed {sha} on {wt.branch} (integration={mode})",
                                 tests=tests, commit_sha=sha, changed_files=files)

    overlap = _dirty_overlap(repo_root, files)
    if overlap:
        return IntegrationResult(
            status="awaiting_review", tests=tests, commit_sha=sha, changed_files=files,
            notes=f"committed {sha}; repo has uncommitted edits to {overlap[:5]} — not merged",
        )
    ok, note = merge_into_base(repo_root, wt.branch, task.get("base_branch") or "main",
                               f"Merge {wt.branch}: {first_line}")
    if not ok:
        return IntegrationResult(status="awaiting_review", tests=tests, commit_sha=sha,
                                 changed_files=files, notes=note)
    if os.environ.get("DEV_AGENT_CLEANUP_AFTER_MERGE", "true").strip().lower() != "false":
        try:
            WorktreeManager(repo_root=repo_root, worktree_root=worktree_root).cleanup(task["id"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"worktree cleanup after merge failed: {e}")
    logger.info(f"task #{task['id']}: merged {wt.branch} → {note[:12]} ({len(files)} files)")
    return IntegrationResult(status="merged", tests=tests, commit_sha=note,
                             changed_files=files, notes=f"merged into {task.get('base_branch') or 'main'} as {note[:12]}")

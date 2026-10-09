"""Post-run integration: test → commit → merge the task branch into main.

Before 2026-09-12 a completed task was stamped `merged` in the DB while its
work stayed in an un-committed worktree (9/9 tasks, 0 commits on main). This
module makes the label true:

  1. Inspect the worktree. No changes → `noop` (task still counts as done).
  2. `name_errors`: undefined names / redefinitions (ruff F821, F811 — the
     same check as `make test-all`'s `names` suite) the patch introduces in
     the Python files it touched → task `failed` (`undefined_name`). A suite
     only catches a dropped binding if a test reaches that line.
     `run_tests`: run pytest for every touched project (`uv run --project`)
     and every project that depends on one (python-shared → every service;
     graph → agent, synthesis, labs). Any failure → task `failed`
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
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
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


def _project_of(path: str) -> str | None:
    parts = Path(path).parts
    if len(parts) >= 2 and parts[0] == "services":
        return f"services/{parts[1]}"
    if len(parts) >= 2 and parts[0] == "packages" and parts[1] == "python-shared":
        return "packages/python-shared"
    return None


def _dependents(root: Path) -> dict[str, set[str]]:
    """project -> projects that declare it as a uv path dependency."""
    deps: dict[str, set[str]] = {}
    for py in [*sorted(root.glob("services/*/pyproject.toml")),
               root / "packages/python-shared/pyproject.toml"]:
        if not py.exists():
            continue
        proj = py.parent.relative_to(root).as_posix()
        sources = tomllib.loads(py.read_text()).get("tool", {}).get("uv", {}).get("sources", {})
        for src in sources.values():
            if isinstance(src, dict) and "path" in src:
                dep = (py.parent / src["path"]).resolve().relative_to(root.resolve()).as_posix()
                deps.setdefault(dep, set()).add(proj)
    return deps


def test_targets(files: list[str], root: Path | None = None) -> list[str]:
    """Project directories (relative) whose pytest suite must pass: every
    touched project, then (with `root`) every project that depends on one,
    transitively. A python-shared change used to run only python-shared's own
    suite although every service imports it."""
    targets: list[str] = []
    for f in files:
        t = _project_of(f)
        if t and t not in targets:
            targets.append(t)
    if root is None:
        return targets
    dependents = _dependents(root)
    seen, frontier, consumers = set(targets), list(targets), set()
    while frontier:
        for d in dependents.get(frontier.pop(), ()):
            if d not in seen:
                seen.add(d)
                consumers.add(d)
                frontier.append(d)
    return targets + sorted(consumers)


_NAME_RULES = "F821,F811"  # undefined name, redefinition of unused name


def _ruff_names(cwd: Path, files: list[str]) -> list[dict[str, Any]]:
    if not files:
        return []
    r = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--ignore-noqa",
         "--select", _NAME_RULES, "--output-format", "json", *files],
        cwd=str(cwd), capture_output=True, text=True,
    )
    if r.returncode not in (0, 1):
        raise RuntimeError(f"ruff failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout or "[]")


def _name_key(cwd: Path, d: dict[str, Any]) -> tuple[str, str, str]:
    rel = Path(d["filename"]).resolve().relative_to(cwd.resolve()).as_posix()
    # "Redefinition of unused `x` from line 3" — line numbers move with any edit.
    return rel, d["code"], re.sub(r" from line \d+", "", d["message"])


def name_errors(wt_path: Path, files: list[str]) -> list[str]:
    """Undefined names / redefinitions the change INTRODUCES in the Python
    files it touched (both checks are per-file, so the touched files are the
    whole blast radius). Errors already present at HEAD don't block a patch;
    `noqa` doesn't hide new ones."""
    py = [f for f in files if f.endswith(".py") and (wt_path / f).is_file()]
    if not py:
        return []
    after = Counter(_name_key(wt_path, d) for d in _ruff_names(wt_path, py))
    if not after:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        base, old = Path(tmp), []
        for f in py:
            r = _git(wt_path, "show", f"HEAD:{f}", check=False)
            if r.returncode == 0:
                (base / f).parent.mkdir(parents=True, exist_ok=True)
                (base / f).write_text(r.stdout)
                old.append(f)
        before = Counter(_name_key(base, d) for d in _ruff_names(base, old))
    new = after - before
    return [f"{f}: {code} {msg}" for (f, code, msg), n in sorted(new.items()) for _ in range(n)]


def _test_env() -> dict[str, str]:
    env = dict(os.environ)
    # The container exports UV_PROJECT_ENVIRONMENT=/workspace/services/dev_agent/.venv.
    # Inherited by `uv run --project <worktree>/services/<svc>`, uv SYNCS THAT
    # PROJECT INTO THE DEV_AGENT VENV — every worktree test run silently
    # replaced dev_agent's own packages (found 2026-09-13 when a reload died with
    # "No module named 'dev_agent'"). Each worktree project gets its own .venv.
    for key in ("UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        env.pop(key, None)
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

    names = name_errors(wt.path, files)
    if names:
        return IntegrationResult(
            status="failed", failure_reason="undefined_name",
            notes="patch introduces undefined/redefined names:\n" + "\n".join(names[:20]),
            tests={"targets": [], "passed": False, "names": names}, changed_files=files,
        )

    tests: dict[str, Any] | None = None
    if task.get("run_tests", True):
        targets = test_targets(files, wt.path)
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


def merge_reviewed_task(repo_root: Path, worktree_root: Path, task: dict[str, Any]) -> tuple[bool, str]:
    """Operator accepted an `awaiting_review` task (web UI / Telegram): merge
    its dev-agent/task-N branch into the base branch with the same dirty-tree
    guard as the auto path. Returns (merged, note). A task whose branch is
    gone (no changes were produced) counts as merged."""
    task_id = int(task["id"])
    base = task.get("base_branch") or "main"
    branch = f"dev-agent/task-{task_id}"
    if _git(repo_root, "rev-parse", "--verify", "--quiet", branch, check=False).returncode != 0:
        return True, "no branch to merge (task produced no changes)"
    files = [f for f in _git(repo_root, "diff", "--name-only", f"{base}...{branch}").stdout.splitlines() if f]
    overlap = _dirty_overlap(repo_root, files)
    if overlap:
        return False, f"repo has uncommitted edits to {overlap[:5]} — not merged; retry when the tree is clean"
    first_line = (task.get("description") or "dev_agent change").strip().splitlines()[0][:72]
    ok, note = merge_into_base(repo_root, branch, base, f"Merge {branch}: {first_line}")
    if not ok:
        return False, note
    try:
        WorktreeManager(repo_root=repo_root, worktree_root=worktree_root).cleanup(task_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"worktree cleanup after reviewed merge failed: {e}")
    return True, f"merged into {base} as {note[:12]}"

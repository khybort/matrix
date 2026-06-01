"""Three live acceptance scenarios from the spec (§9.1).

These tests REQUIRE a real ANTHROPIC_API_KEY and a running stack. Skipped
when prerequisites are missing. Run via:

    cd services/dev_agent && uv run pytest -v -m live

Cost: ~$0.10 in API spend, ~5 minutes wall time.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

pytestmark = pytest.mark.live


def _ensure_alive() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    try:
        r = httpx.get("http://localhost:8009/healthz", timeout=2.0)
        r.raise_for_status()
    except Exception:
        pytest.skip("dev_agent not reachable on localhost:8009; run `make up-dev`")


def _wait_for_terminal(task_id: int, timeout_s: int = 300) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = httpx.get(f"http://localhost:8009/tasks/{task_id}", timeout=5.0)
        r.raise_for_status()
        body = r.json()
        if body["status"] in {"awaiting_review", "failed", "merged", "discarded"}:
            return body
        time.sleep(2)
    pytest.fail(f"task {task_id} did not reach terminal status within {timeout_s}s")


def test_acceptance_slash_flow():
    """Scenario 1: simple task succeeds, worktree dirty, no commit."""
    _ensure_alive()
    r = httpx.post(
        "http://localhost:8009/tasks",
        json={
            "description": (
                "Add the line 'this is a smoke test marker' as the last "
                "non-empty line of README.md"
            ),
            "source": "manual",
            "auto_commit": False,
            "run_tests": False,
            "max_turns": 15,
            "touches_files": ["README.md"],
        },
        timeout=5.0,
    )
    assert r.status_code == 201
    task_id = r.json()["id"]
    final = _wait_for_terminal(task_id)
    assert final["status"] == "awaiting_review", f"unexpected status: {final}"
    assert final["worktree_path"] is not None


@pytest.mark.skip(reason="FORBIDDEN_PATHS open since 2026-05-26 — strategy edits allowed")
def test_acceptance_trading_gate_blocks():
    """Historical scenario: default-deny gate. Re-enable when FORBIDDEN_PATHS is repopulated."""
    _ensure_alive()
    r = httpx.post(
        "http://localhost:8009/tasks",
        json={
            "description": (
                "Add a new indicator function to services/strategy/momentum.py "
                "named foo()."
            ),
            "source": "manual",
            "auto_commit": False,
            "run_tests": False,
            "max_turns": 10,
            "touches_files": ["services/strategy/momentum.py"],
        },
        timeout=5.0,
    )
    assert r.status_code == 201
    task_id = r.json()["id"]
    final = _wait_for_terminal(task_id)
    assert final["status"] == "failed"
    assert final["failure_reason"] == "trading_path_violation"


def test_acceptance_lesson_loop():
    """Scenario 3: a deliberately failing task produces a draft lesson; approving
    it means subsequent retrieval includes it.

    Simulated failure: max_turns=1, too low for any non-trivial Claude run."""
    _ensure_alive()
    r = httpx.post(
        "http://localhost:8009/tasks",
        json={
            "description": "Refactor the entire ingestion service. Use any approach you like.",
            "source": "manual",
            "max_turns": 1,
            "auto_commit": False,
            "run_tests": False,
        },
        timeout=5.0,
    )
    task_id = r.json()["id"]
    final = _wait_for_terminal(task_id)
    assert final["status"] == "failed"
    assert final["failure_reason"] in {"max_turns", "tool_loop"}

    drafts = httpx.get("http://localhost:8009/lessons?status=draft").json()
    assert any(l.get("origin_task_id") == task_id for l in drafts), drafts
    draft = next(l for l in drafts if l.get("origin_task_id") == task_id)
    httpx.post(
        f"http://localhost:8009/lessons/{draft['id']}/approve",
        json={"by": "test"},
    ).raise_for_status()

    active = httpx.get("http://localhost:8009/lessons?status=active").json()
    assert any(l["id"] == draft["id"] for l in active)

"""tests/test_prompt.py"""

from __future__ import annotations

from dev_agent.prompt import build_system_prompt


def test_base_layer_mentions_forbidden_paths():
    sp = build_system_prompt(
        task_description="x",
        lessons=[],
        codebase_ctx="",
        auto_commit=False, auto_pr=False, run_tests=True, max_turns=50,
        handoff_snapshot=None,
        touches_files=[],
    )
    assert "FORBIDDEN_PATHS is empty" in sp
    assert "services/strategy" in sp
    assert "services/agent" in sp
    assert "services/execution" in sp
    assert "services/execution (certificate gates" in sp


def test_lesson_layer_includes_all_passed_lessons():
    lessons = [
        {"topic": "t1", "anti_pattern": "ap1", "correct_approach": "ca1"},
        {"topic": "t2", "anti_pattern": None, "correct_approach": "ca2"},
    ]
    sp = build_system_prompt(
        task_description="x", lessons=lessons, codebase_ctx="", auto_commit=False,
        auto_pr=False, run_tests=True, max_turns=50, handoff_snapshot=None, touches_files=[],
    )
    assert "t1" in sp and "t2" in sp
    assert "ap1" in sp


def test_handoff_layer_only_when_snapshot_present():
    sp_no = build_system_prompt(
        task_description="x", lessons=[], codebase_ctx="", auto_commit=False,
        auto_pr=False, run_tests=True, max_turns=50, handoff_snapshot=None, touches_files=[],
    )
    assert "HANDOFF_CONTEXT" not in sp_no

    sp_yes = build_system_prompt(
        task_description="x", lessons=[], codebase_ctx="", auto_commit=False,
        auto_pr=False, run_tests=True, max_turns=50,
        handoff_snapshot={"summary": "prior session worked on Y"},
        touches_files=[],
    )
    assert "HANDOFF_CONTEXT" in sp_yes
    assert "prior session worked on Y" in sp_yes


def test_constraints_layer_reflects_flags():
    sp = build_system_prompt(
        task_description="x", lessons=[], codebase_ctx="",
        auto_commit=True, auto_pr=False, run_tests=False, max_turns=99,
        handoff_snapshot=None, touches_files=[],
    )
    assert "auto_commit=True" in sp
    assert "max_turns=99" in sp

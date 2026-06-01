"""Cursor Auto backend for dev_agent — CLI stream-json (subscription login).

The cursor-sdk bridge requires CURSOR_API_KEY; CLI `agent login` tokens in
auth.json work with `cursor agent -p --output-format stream-json` instead.
Trading-path safety: we kill the subprocess on forbidden tool_call.started;
post-hoc git checks in worker.py remain the backstop.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import asyncpg

from dev_agent.sdk_runner import RunResult
from dev_agent.safety import SafetyError, ToolLoopDetector, check_tool_call


def _cursor_bin() -> str:
    import os

    return os.environ.get("MATRIX_CURSOR_BIN", "cursor")


def _call_timeout_s() -> float:
    import os

    return float(os.environ.get("MATRIX_LLM_CALL_TIMEOUT_S", "600"))


def _combine_prompt(*, system: str | None, user: str) -> str:
    if system:
        return f"{system.strip()}\n\n---\n\n{user}"
    return user


def _tool_from_cli_event(ev: dict) -> tuple[str, dict] | None:
    tc = ev.get("tool_call") or {}
    if "editToolCall" in tc:
        args = tc["editToolCall"].get("args") or {}
        return "Write", {"file_path": args.get("path", "")}
    if "shellToolCall" in tc:
        args = tc["shellToolCall"].get("args") or {}
        return "Bash", {"command": args.get("command", "")}
    if "readToolCall" in tc:
        args = tc["readToolCall"].get("args") or {}
        return "Read", {"file_path": args.get("path", "")}
    return None


async def run_task_with_cursor(
    *,
    pool: asyncpg.Pool,
    task_id: int,
    run_id: int,
    cwd: Path,
    max_turns: int,
    cost_cap_usd: float,
    prompt: str,
    system: str | None,
) -> RunResult:
    from dev_agent.events import write_event

    loops = ToolLoopDetector(threshold=5)
    files_modified: set[str] = set()
    seq = await pool.fetchval(
        "SELECT COALESCE(MAX(seq), 0) FROM dev_task_events WHERE task_id=$1",
        task_id,
    )
    seq = int(seq or 0)
    turns = 0
    failure_reason: str | None = None

    combined = _combine_prompt(system=system, user=prompt)
    cmd = [
        _cursor_bin(),
        "agent",
        "-p",
        combined,
        "--model",
        "auto",
        "--output-format",
        "stream-json",
        "--trust",
        "--workspace",
        str(cwd),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
    )

    async def _read_stream() -> None:
        nonlocal seq, turns, failure_reason
        assert proc.stdout is not None
        while True:
            cancel = await pool.fetchval(
                "SELECT cancel_requested FROM dev_tasks WHERE id=$1", task_id
            )
            if cancel:
                failure_reason = "user_killed"
                proc.kill()
                return

            line = await proc.stdout.readline()
            if not line:
                return
            raw = line.decode(errors="replace").strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue

            ev_type = ev.get("type", "")
            if ev_type == "assistant":
                text_parts = []
                for block in (ev.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                if text_parts:
                    seq += 1
                    await write_event(
                        pool,
                        task_id=task_id,
                        run_id=run_id,
                        seq=seq,
                        event_type="assistant_text",
                        payload={"text": "".join(text_parts)},
                    )
            elif ev_type == "tool_call" and ev.get("subtype") == "started":
                parsed = _tool_from_cli_event(ev)
                if parsed:
                    tool_name, params = parsed
                    try:
                        check_tool_call(tool_name, params)
                        loops.record(tool_name, params)
                    except SafetyError as e:
                        failure_reason = e.reason
                        proc.kill()
                        return
                    turns += 1
                    if turns >= max_turns:
                        failure_reason = "max_turns"
                        proc.kill()
                        return
                    fp = params.get("file_path")
                    if fp and tool_name in ("Write", "Edit"):
                        files_modified.add(fp)
                    seq += 1
                    await write_event(
                        pool,
                        task_id=task_id,
                        run_id=run_id,
                        seq=seq,
                        event_type="tool_use",
                        payload={
                            "name": tool_name,
                            "params": params,
                            "id": ev.get("call_id", ""),
                        },
                    )
            elif ev_type == "result":
                if ev.get("is_error"):
                    failure_reason = failure_reason or "cursor_agent_error"

    try:
        await asyncio.wait_for(_read_stream(), timeout=_call_timeout_s())
    except TimeoutError:
        failure_reason = failure_reason or "timeout"
        proc.kill()

    await proc.wait()
    if proc.returncode not in (0, None) and failure_reason is None and turns == 0:
        err = (await proc.stderr.read() if proc.stderr else b"").decode(errors="replace")
        if err.strip():
            failure_reason = "cursor_cli_error"

    return RunResult(
        completed=failure_reason is None,
        failure_reason=failure_reason,
        total_cost_usd=0.0,
        event_count=seq,
        files_modified=sorted(files_modified),
    )

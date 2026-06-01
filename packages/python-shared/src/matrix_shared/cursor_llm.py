"""Cursor backend for Matrix LLM calls (always model=auto).

Activated by `make llm-cursor` (`MATRIX_LLM_BACKEND=cursor`). Auth mirrors the
Claude subscription path: prefer `cursor agent login` (browser flow, creds
stored locally) rather than billing API keys. `CURSOR_API_KEY` remains an
optional override for CI/Docker when mounting login state is impractical.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger

from matrix_shared.agent_runtime.mcp_http import McpHttpServer
from matrix_shared.agent_runtime.ratelimit import AgentRateLimiter
from matrix_shared.agent_runtime.runtime import AgentEvent
from matrix_shared.agent_runtime.tool import ToolRegistry

CURSOR_MODEL_AUTO = "auto"

_auth_cache_until = 0.0
_auth_cache_logged_in: bool | None = None


def resolve_cursor_model(model: str | None) -> str:
    """Cursor backend always uses Auto; tier pins and caller model ids are ignored."""
    return CURSOR_MODEL_AUTO


def _cursor_bin() -> str:
    return os.environ.get("MATRIX_CURSOR_BIN", "cursor")


def _cursor_api_key() -> str | None:
    key = os.environ.get("CURSOR_API_KEY")
    return key.strip() if key else None


def _auth_cache_ttl_s() -> float:
    return float(os.environ.get("MATRIX_CURSOR_AUTH_CACHE_S", "30"))


def _status_output_logged_in(text: str) -> bool:
    lower = text.lower()
    if "not logged in" in lower or "authentication required" in lower:
        return False
    return bool(lower.strip())


def _cli_logged_in_sync() -> bool:
    """True when `cursor agent status` shows an active CLI login (not API key)."""
    global _auth_cache_until, _auth_cache_logged_in
    now = time.monotonic()
    if _auth_cache_logged_in is not None and now < _auth_cache_until:
        return _auth_cache_logged_in
    timeout = float(os.environ.get("MATRIX_CURSOR_STATUS_TIMEOUT_S", "8"))
    try:
        completed = subprocess.run(
            [_cursor_bin(), "agent", "status"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        text = (completed.stdout or completed.stderr or b"").decode(errors="replace")
        logged_in = completed.returncode == 0 and _status_output_logged_in(text)
    except Exception:
        logged_in = False
    _auth_cache_logged_in = logged_in
    _auth_cache_until = now + _auth_cache_ttl_s()
    return logged_in


async def _cli_logged_in() -> bool:
    global _auth_cache_until, _auth_cache_logged_in
    now = time.monotonic()
    if _auth_cache_logged_in is not None and now < _auth_cache_until:
        return _auth_cache_logged_in
    try:
        logged_in = await asyncio.wait_for(
            _spawn_status(),
            timeout=float(os.environ.get("MATRIX_CURSOR_STATUS_TIMEOUT_S", "8")),
        )
    except Exception:
        logged_in = False
    _auth_cache_logged_in = logged_in
    _auth_cache_until = now + _auth_cache_ttl_s()
    return logged_in


async def _spawn_status() -> bool:
    proc = await asyncio.create_subprocess_exec(
        _cursor_bin(),
        "agent",
        "status",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = (out or b"").decode(errors="replace")
    if proc.returncode != 0:
        return False
    return _status_output_logged_in(text)


def cursor_enabled() -> bool:
    if _cursor_api_key():
        return True
    if os.environ.get("MATRIX_CURSOR_REQUIRE_CLI_LOGIN", "1").strip().lower() in (
        "0",
        "false",
        "no",
    ):
        return False
    return _cli_logged_in_sync()


def _cursor_cwd() -> str:
    return os.environ.get("MATRIX_CURSOR_CWD", "/tmp")


def _combine_prompt(*, system: str | None, user: str) -> str:
    if system:
        return f"{system.strip()}\n\n---\n\n{user}"
    return user


def _call_timeout_s() -> float:
    return float(os.environ.get("MATRIX_LLM_CALL_TIMEOUT_S", "45"))


def _parse_cli_json(stdout: bytes) -> tuple[str | None, bool]:
    """Parse `cursor agent -p --output-format json` envelope."""
    raw = stdout.decode(errors="replace").strip()
    if not raw:
        return None, True
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return raw or None, False
    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            return None, True
        for key in ("result", "text", "output"):
            val = envelope.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip(), False
        return None, bool(envelope.get("error"))
    if isinstance(envelope, str):
        return envelope.strip() or None, False
    return raw or None, False


async def _cursor_cli_print(*, prompt: str, cwd: str) -> tuple[str | None, bool]:
    cmd = [
        _cursor_bin(),
        "agent",
        "-p",
        prompt,
        "--model",
        CURSOR_MODEL_AUTO,
        "--output-format",
        "json",
        "--trust",
        "--workspace",
        cwd,
    ]
    api_key = _cursor_api_key()
    if api_key:
        cmd.extend(["--api-key", api_key])
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_call_timeout_s())
    except TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning(f"cursor_llm: CLI timed out after {_call_timeout_s():.0f}s")
        return None, True
    if proc.returncode != 0:
        err = (stderr or stdout or b"").decode(errors="replace").strip()
        logger.warning(f"cursor_llm: CLI exit {proc.returncode}: {err[:300]}")
        return None, True
    return _parse_cli_json(stdout or b"")


async def cursor_single_shot(
    *,
    user: str,
    system: str | None = None,
    model: str | None = None,
) -> tuple[str | None, bool]:
    """One-shot text via `cursor agent -p` (subscription login or optional API key)."""
    if not cursor_enabled():
        return None, True
    prompt = _combine_prompt(system=system, user=user)
    cwd = _cursor_cwd()
    text, is_err = await _cursor_cli_print(prompt=prompt, cwd=cwd)
    logger.info(
        "agent.usage session=single_shot backend=cursor model={m} status={s}",
        m=CURSOR_MODEL_AUTO,
        s="error" if is_err else "finished",
    )
    return text, is_err


def _cursor_mcp_config(url: str, server_name: str) -> dict[str, Any]:
    from cursor_sdk import HttpMcpServerConfig

    return {server_name: HttpMcpServerConfig(url=url)}


async def _map_cursor_messages(run: Any) -> AsyncIterator[AgentEvent]:
    async for message in run.messages():
        mtype = getattr(message, "type", None)
        if mtype == "assistant":
            content = getattr(getattr(message, "message", None), "content", None) or []
            for block in content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    yield AgentEvent("assistant_text", {"text": getattr(block, "text", "")})
                elif btype == "tool_use":
                    yield AgentEvent(
                        "tool_use",
                        {
                            "name": getattr(block, "name", ""),
                            "params": getattr(block, "input", {}),
                            "id": getattr(block, "id", ""),
                        },
                    )
        elif mtype == "thinking":
            yield AgentEvent("thinking", {"text": getattr(message, "text", "")})
        elif mtype == "tool_call":
            status = getattr(message, "status", "")
            if status == "running":
                yield AgentEvent(
                    "tool_use",
                    {
                        "name": getattr(message, "name", ""),
                        "params": getattr(message, "args", {}) or {},
                        "id": getattr(message, "call_id", ""),
                    },
                )
            elif status in ("completed", "error"):
                yield AgentEvent(
                    "tool_result",
                    {
                        "tool_use_id": getattr(message, "call_id", ""),
                        "content": str(getattr(message, "result", "")),
                        "is_error": status == "error",
                    },
                )


@asynccontextmanager
async def _cursor_http_mcp(registry: ToolRegistry | None, server_name: str | None):
    if registry is not None and server_name:
        server = McpHttpServer(registry=registry)
        url = await server.start()
        try:
            yield _cursor_mcp_config(url, server_name)
        finally:
            await server.stop()
    else:
        yield None


def _sdk_agent_options(*, cwd: str, mcp_servers: dict[str, Any] | None) -> Any:
    from cursor_sdk import AgentOptions, LocalAgentOptions

    kwargs: dict[str, Any] = {
        "model": CURSOR_MODEL_AUTO,
        "local": LocalAgentOptions(cwd=cwd),
    }
    api_key = _cursor_api_key()
    if api_key:
        kwargs["api_key"] = api_key
    if mcp_servers:
        kwargs["mcp_servers"] = mcp_servers
    return AgentOptions(**kwargs)


async def cursor_agent_stream(
    *,
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    tool_registry: ToolRegistry | None = None,
    mcp_server_name: str | None = None,
    max_turns: int = 12,
    session_id: str = "agent",
    limiter: AgentRateLimiter | None = None,
) -> AsyncIterator[AgentEvent]:
    """Multi-step Cursor agent run via cursor-sdk; yields normalized AgentEvents."""
    if not cursor_enabled():
        return

    cwd = _cursor_cwd()
    turns = 0

    from contextlib import nullcontext

    slot = limiter.slot() if limiter is not None else nullcontext()
    async with slot, _cursor_http_mcp(tool_registry, mcp_server_name) as mcp_servers:
        try:
            from cursor_sdk import AsyncClient

            async with await AsyncClient.launch_bridge(workspace=cwd) as client:
                create_kwargs = _sdk_agent_options(cwd=cwd, mcp_servers=mcp_servers)
                async with await client.agents.create(**create_kwargs) as agent:
                    send_prompt = _combine_prompt(system=system, user=prompt)
                    run = await agent.send(send_prompt)
                    async for ev in _map_cursor_messages(run):
                        if ev.type == "tool_use":
                            turns += 1
                            if turns >= max_turns:
                                yield AgentEvent(
                                    "result",
                                    {
                                        "total_cost_usd": None,
                                        "num_turns": turns,
                                        "is_error": True,
                                        "reason": "max_turns",
                                    },
                                )
                                return
                        yield ev

                    result = await run.wait()
                    status = getattr(result, "status", None)
                    yield AgentEvent(
                        "result",
                        {
                            "total_cost_usd": None,
                            "num_turns": turns,
                            "is_error": status not in (None, "finished"),
                            "model": CURSOR_MODEL_AUTO,
                        },
                    )
                    logger.info(
                        "agent.usage session={s} backend=cursor model={m} turns={t} status={st}",
                        s=session_id,
                        m=CURSOR_MODEL_AUTO,
                        t=turns,
                        st=status,
                    )
        except Exception as e:
            logger.warning(f"cursor_llm agent stream failed: {e}")
            yield AgentEvent(
                "result",
                {
                    "total_cost_usd": None,
                    "num_turns": turns,
                    "is_error": True,
                    "reason": str(e),
                },
            )

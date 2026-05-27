"""Consume the Brain's SSE /chat stream for Telegram Q&A.

Telegram can't stream token-by-token cheaply, so we accumulate the full
answer (and the tool names used) and return them for a single message edit.
Session continuity is keyed on the Telegram chat via `external_ref`.
"""

from __future__ import annotations

import os

import httpx
import orjson
from loguru import logger

BRAIN_URL = os.environ.get("BRAIN_URL", "http://brain:3032")


async def ask_brain(message: str, *, external_ref: str, timeout: float = 120.0) -> str:
    """Send one question to the Brain; return the assembled answer text."""
    payload = {"message": message, "surface": "telegram", "external_ref": external_ref}
    answer: list[str] = []
    tools: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            async with client.stream("POST", f"{BRAIN_URL}/chat", json=payload) as resp:
                resp.raise_for_status()
                event = "message"
                data_lines: list[str] = []
                async for line in resp.aiter_lines():
                    if line == "":  # frame boundary
                        _dispatch(event, "\n".join(data_lines), answer, tools)
                        event, data_lines = "message", []
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].removeprefix(" "))
                if data_lines:
                    _dispatch(event, "\n".join(data_lines), answer, tools)
    except Exception as e:
        logger.exception("ask_brain failed")
        return f"⚠️ Brain unreachable: {e}"

    text = "".join(answer).strip() or "(no answer)"
    if tools:
        text += "\n\n_tools: " + ", ".join(dict.fromkeys(tools)) + "_"
    return text


def _dispatch(event: str, data: str, answer: list[str], tools: list[str]) -> None:
    if event == "token":
        answer.append(data)
    elif event == "tool_call":
        try:
            tools.append(str(orjson.loads(data).get("name", "")).replace("mcp__matrix__", ""))
        except orjson.JSONDecodeError:
            pass
    elif event == "error":
        answer.append(f"\n⚠️ {data}")

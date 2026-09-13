"""Thin HTTP client for the dev_agent review API (Telegram → dev_agent).

The operator's only remaining touch-point for code changes is accepting or
discarding `awaiting_review` tasks; this lets that happen from the phone
instead of the web UI. Base URL: DEV_AGENT_API_URL (compose-internal
http://dev_agent:8009 by default). Every call returns a human-readable line;
failures never raise into the bot handler.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger

DEFAULT_URL = "http://dev_agent:8009"
TIMEOUT_S = float(os.environ.get("DEV_AGENT_API_TIMEOUT_S", "120"))  # accept runs a git merge


def base_url() -> str:
    return os.environ.get("DEV_AGENT_API_URL", DEFAULT_URL).rstrip("/")


async def _call(method: str, path: str, *, json: dict[str, Any] | None = None,
                client: httpx.AsyncClient | None = None) -> tuple[bool, Any]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S))
    try:
        r = await client.request(method, f"{base_url()}{path}", json=json)
        body: Any
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if r.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else body
            return False, str(detail)[:400]
        return True, body
    except httpx.HTTPError as e:
        logger.warning(f"dev_agent api {method} {path} failed: {e}")
        return False, f"dev_agent unreachable ({type(e).__name__})"
    finally:
        if own:
            await client.aclose()


def format_task_line(t: dict[str, Any]) -> str:
    desc = (t.get("description") or "").strip().splitlines()
    title = desc[0][:80] if desc else "no description"
    notes = (t.get("review_notes") or "").strip()
    cost = t.get("total_cost_usd")
    line = f"#{t.get('id')} {title}"
    if cost is not None:
        line += f" (${float(cost):.2f})"
    if notes:
        line += f"\n    {notes[:160]}"
    return line


async def list_awaiting(client: httpx.AsyncClient | None = None) -> str:
    ok, body = await _call("GET", "/tasks?status=awaiting_review", client=client)
    if not ok:
        return f"✗ {body}"
    tasks = body if isinstance(body, list) else []
    if not tasks:
        return "No dev_agent tasks awaiting review."
    lines = ["🧩 awaiting review:"] + [format_task_line(t) for t in tasks[:15]]
    lines.append("/dev_accept <id> · /dev_discard <id> · /dev_revise <id> <notes>")
    return "\n".join(lines)


async def accept(task_id: int, *, by: str, client: httpx.AsyncClient | None = None) -> str:
    ok, body = await _call("POST", f"/tasks/{task_id}/accept", json={"by": by}, client=client)
    if not ok:
        return f"✗ task #{task_id} not merged: {body}"
    note = body.get("note", "") if isinstance(body, dict) else ""
    return f"✅ task #{task_id} merged. {note}".strip()


async def discard(task_id: int, *, by: str, client: httpx.AsyncClient | None = None) -> str:
    ok, body = await _call("POST", f"/tasks/{task_id}/discard", json={"by": by}, client=client)
    return f"🗑 task #{task_id} discarded." if ok else f"✗ task #{task_id}: {body}"


async def revise(task_id: int, notes: str, *, client: httpx.AsyncClient | None = None) -> str:
    ok, body = await _call("POST", f"/tasks/{task_id}/revise", json={"notes": notes}, client=client)
    return f"🔁 task #{task_id} re-queued with your notes." if ok else f"✗ task #{task_id}: {body}"

"""dev_client → dev_agent REST: message formatting for success/failure paths,
against an httpx MockTransport (no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from notify import dev_client as DC

pytestmark = pytest.mark.asyncio


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_list_awaiting_formats_tasks(monkeypatch):
    monkeypatch.setenv("DEV_AGENT_API_URL", "http://dev/")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/tasks" and req.url.params["status"] == "awaiting_review"
        return httpx.Response(200, json=[{"id": 7, "description": "fix x\n\nlong body", "total_cost_usd": 1.234,
                                          "review_notes": "committed abc; awaiting review"}])

    async with _client(handler) as c:
        out = await DC.list_awaiting(client=c)
    assert "#7 fix x ($1.23)" in out and "committed abc" in out and "/dev_accept" in out


async def test_list_awaiting_empty_and_unreachable():
    async with _client(lambda r: httpx.Response(200, json=[])) as c:
        assert "No dev_agent tasks" in await DC.list_awaiting(client=c)

    def boom(req):
        raise httpx.ConnectError("refused")
    async with _client(boom) as c:
        assert "unreachable" in await DC.list_awaiting(client=c)


async def test_accept_success_and_409_detail():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST" and req.url.path == "/tasks/7/accept"
        assert json.loads(req.content)["by"] == "telegram:1"
        return httpx.Response(200, json={"status": "merged", "note": "merged into main as abc123"})
    async with _client(handler) as c:
        out = await DC.accept(7, by="telegram:1", client=c)
    assert out.startswith("✅ task #7 merged") and "abc123" in out

    async with _client(lambda r: httpx.Response(409, json={"detail": "repo has uncommitted edits"})) as c:
        out = await DC.accept(7, by="telegram:1", client=c)
    assert out.startswith("✗") and "uncommitted" in out


async def test_discard_and_revise():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"status": "ok"})
    async with _client(handler) as c:
        assert "discarded" in await DC.discard(3, by="telegram:1", client=c)
        assert "re-queued" in await DC.revise(3, "use Wilson bound", client=c)
    assert seen == [("/tasks/3/discard", {"by": "telegram:1"}), ("/tasks/3/revise", {"notes": "use Wilson bound"})]

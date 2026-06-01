"""In-process MCP HTTP bridge for Cursor tool loops."""

from __future__ import annotations

import json

import pytest

from matrix_shared.agent_runtime.mcp_http import McpHttpServer
from matrix_shared.agent_runtime.tool import Tool, ToolRegistry


@pytest.mark.asyncio
async def test_mcp_http_tools_list_and_call():
    async def _echo(params: dict) -> dict:
        return {"ok": True, "value": params.get("x")}

    registry = ToolRegistry()
    registry.add(
        Tool(
            name="echo",
            description="echo x",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=_echo,
        )
    )
    server = McpHttpServer(registry=registry)
    url = await server.start()
    assert url.endswith("/mcp")

    reader, writer = await __import__("asyncio").open_connection("127.0.0.1", server.port)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    req = (
        f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    writer.write(req)
    await writer.drain()
    raw = await reader.read(4096)
    writer.close()
    await writer.wait_closed()
    await server.stop()

    assert b'"echo"' in raw
    payload = json.loads(raw.split(b"\r\n\r\n", 1)[1])
    tools = payload["result"]["tools"]
    assert tools[0]["name"] == "echo"

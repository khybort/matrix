"""Minimal in-process MCP HTTP server for a ToolRegistry.

Cursor's SDK accepts HttpMcpServerConfig URLs. Claude Code uses in-process
MCP via claude_agent_sdk; this bridge keeps Matrix tool handlers in-process
while exposing them over HTTP for the Cursor agent runtime.
"""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from matrix_shared.agent_runtime.tool import ToolRegistry


def _tool_specs(registry: ToolRegistry) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
        }
        for t in registry.all()
    ]


async def _handle_tool_call(registry: ToolRegistry, name: str, arguments: dict) -> dict:
    tool = registry.get(name)
    result = await tool.handler(arguments or {})
    text = json.dumps(result, default=str)
    return {"content": [{"type": "text", "text": text}]}


async def _dispatch(registry: ToolRegistry, body: dict) -> dict:
    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "matrix-mcp-http", "version": "1.0.0"},
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _tool_specs(registry)}}
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        payload = await _handle_tool_call(registry, name, arguments)
        return {"jsonrpc": "2.0", "id": req_id, "result": payload}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"unsupported method: {method}"},
    }


def _http_response(status: int, body: bytes, *, content_type: str = "application/json") -> bytes:
    reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"}[
        status
    ]
    header = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    return header + body


async def _read_http_request(reader: asyncio.StreamReader) -> tuple[str, str, dict | None]:
    header_lines: list[str] = []
    while True:
        line = await reader.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        header_lines.append(line.decode("latin-1").strip())
    if not header_lines:
        return "", "", None
    parts = header_lines[0].split()
    method = parts[0] if parts else ""
    path = parts[1] if len(parts) > 1 else ""
    content_length = 0
    for h in header_lines[1:]:
        if h.lower().startswith("content-length:"):
            content_length = int(h.split(":", 1)[1].strip())
    body = None
    if content_length > 0:
        raw = await reader.readexactly(content_length)
        body = json.loads(raw.decode())
    return method, path, body


async def _handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, registry: ToolRegistry
) -> None:
    try:
        method, path, body = await _read_http_request(reader)
        if method != "POST" or not path.rstrip("/").endswith("/mcp"):
            writer.write(_http_response(404, b'{"error":"not found"}'))
            await writer.drain()
            return
        if body is None:
            writer.write(_http_response(400, b'{"error":"missing body"}'))
            await writer.drain()
            return
        result = await _dispatch(registry, body)
        payload = json.dumps(result).encode()
        writer.write(_http_response(200, payload))
        await writer.drain()
    except Exception as e:
        logger.warning(f"mcp_http client error: {e}")
        try:
            writer.write(_http_response(500, json.dumps({"error": str(e)}).encode()))
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@dataclass
class McpHttpServer:
    registry: ToolRegistry
    host: str = "127.0.0.1"
    port: int = 0
    _server: asyncio.Server | None = field(default=None, init=False, repr=False)

    @property
    def url(self) -> str:
        if self._server is None or self.port == 0:
            raise RuntimeError("McpHttpServer not started")
        return f"http://{self.host}:{self.port}/mcp"

    async def start(self) -> str:
        if self._server is not None:
            return self.url
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, 0))
        sock.listen(128)
        self.port = sock.getsockname()[1]
        sock.close()

        self._server = await asyncio.start_server(
            lambda r, w: _handle_client(r, w, self.registry),
            host=self.host,
            port=self.port,
        )
        return self.url

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self.port = 0

"""Server-Sent Events framing for the /chat stream."""

from __future__ import annotations

from typing import Any

import orjson


def sse_frame(event: str, data: Any) -> str:
    """Format one SSE frame. `data` is JSON-encoded unless already a string.

    Per the SSE spec each line of the payload gets its own `data:` field, so a
    multi-line string (assistant text can contain newlines) frames correctly.
    """
    payload = data if isinstance(data, str) else orjson.dumps(data).decode()
    data_lines = "\n".join(f"data: {line}" for line in payload.split("\n"))
    return f"event: {event}\n{data_lines}\n\n"

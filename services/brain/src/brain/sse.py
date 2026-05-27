"""Server-Sent Events framing for the /chat stream."""

from __future__ import annotations

from typing import Any

import orjson


def sse_frame(event: str, data: Any) -> str:
    """Format one SSE frame. `data` is JSON-encoded unless already a string."""
    payload = data if isinstance(data, str) else orjson.dumps(data).decode()
    return f"event: {event}\ndata: {payload}\n\n"

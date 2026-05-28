"""FastAPI routes for the Brain — SSE /chat + session inspection."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from brain import sessions
from brain.history import format_history
from brain.runtime import BrainRuntime
from brain.sse import sse_frame


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    surface: str = "api"
    external_ref: str | None = None
    # Per-request model override. Omit to use BRAIN_MODEL (default
    # claude-sonnet-4-6). Pass "claude-opus-4-7" for a single hard question
    # without flipping the global default.
    model: str | None = None


class SessionCreate(BaseModel):
    surface: str = "api"
    external_ref: str | None = None
    title: str | None = None


def build_app(*, shared_pool: asyncpg.Pool, runtime: BrainRuntime) -> FastAPI:
    app = FastAPI(title="matrix-brain")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/sessions", status_code=201)
    async def create_session(payload: SessionCreate) -> dict[str, str]:
        sid = await sessions.create_session(
            shared_pool,
            surface=payload.surface,
            external_ref=payload.external_ref,
            title=payload.title,
        )
        return {"id": sid}

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        s = await sessions.get_session(shared_pool, session_id)
        if not s:
            raise HTTPException(status_code=404, detail="session not found")
        return s

    @app.post("/chat")
    async def chat(payload: ChatRequest) -> StreamingResponse:
        # Resolve the session (by id, by external ref, or create fresh).
        if payload.session_id:
            session_id = payload.session_id
        elif payload.external_ref:
            session_id = await sessions.get_or_create_by_ref(
                shared_pool, surface=payload.surface, external_ref=payload.external_ref
            )
        else:
            session_id = await sessions.create_session(
                shared_pool, surface=payload.surface
            )

        history = await sessions.load_recent(shared_pool, session_id)
        prompt = format_history(history, payload.message)
        await sessions.append_message(
            shared_pool, session_id, role="user", content=payload.message
        )

        async def stream():
            yield sse_frame("session", {"session_id": session_id})
            answer_parts: list[str] = []
            tool_trace: list[dict] = []
            try:
                async for ev in runtime.run(
                    prompt, session_id=session_id, model=payload.model
                ):
                    if ev.type == "assistant_text":
                        text = ev.payload.get("text", "")
                        answer_parts.append(text)
                        yield sse_frame("token", text)
                    elif ev.type == "tool_use":
                        tool_trace.append(
                            {"name": ev.payload.get("name"), "params": ev.payload.get("params")}
                        )
                        yield sse_frame("tool_call", ev.payload)
                    elif ev.type == "tool_result":
                        yield sse_frame("tool_result", ev.payload)
                    elif ev.type == "thinking":
                        yield sse_frame("thinking", ev.payload)
                    elif ev.type == "result":
                        yield sse_frame("result", ev.payload)
            except Exception as e:  # don't leave the stream hanging on error
                logger.exception("brain chat stream error")
                yield sse_frame("error", {"message": str(e)})

            answer = "".join(answer_parts).strip()
            if answer:
                await sessions.append_message(
                    shared_pool,
                    session_id,
                    role="assistant",
                    content=answer,
                    tool_calls=tool_trace or None,
                )
            await sessions.touch(shared_pool, session_id)
            yield sse_frame("done", {"session_id": session_id})

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app

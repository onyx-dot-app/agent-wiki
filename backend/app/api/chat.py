"""FastAPI port of ``app/api/chat.py``.

The 4 session-CRUD routes land in Phase 3 (plain JSON). The SSE
endpoint ``POST /api/chat/messages`` lands here in Phase 4 — async
streaming so the model worker isn't pinned to one OS thread per
in-flight chat the way the Flask sync-worker setup is today.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from app.auth import User
from app.auth.deps import require_user
from app.chat import sessions as sessions_repo
from app.llm.agents.chat import (
    chat_agent_scope,
    messages_from_history,
    run_chat_stream,
)
from app.llm.errors import LLMError
from app.models.chat import (
    ChatMessageOut, ChatSessionDetail, ChatSessionOut, SendChatRequest,
)
from app.tasks.chat_title import generate_chat_title
from app.tracing import trace_flow

router = APIRouter()
log = logging.getLogger(__name__)


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode("utf-8")


def _session_out(row: dict[str, Any]) -> ChatSessionOut:
    return ChatSessionOut(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("/sessions", response_model=list[ChatSessionOut])
def list_sessions(user: User = Depends(require_user)) -> list[ChatSessionOut]:
    rows = sessions_repo.list_for_user(user.id)
    return [_session_out(r) for r in rows]


@router.post(
    "/sessions",
    response_model=ChatSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_session(user: User = Depends(require_user)) -> ChatSessionOut:
    row = sessions_repo.create(user.id)
    return _session_out(row)


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
def get_session(
    session_id: str, user: User = Depends(require_user),
) -> ChatSessionDetail:
    row = sessions_repo.get(session_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = sessions_repo.get_messages(session_id)
    return ChatSessionDetail(
        session=ChatSessionOut(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        ),
        messages=[
            ChatMessageOut(
                id=m["id"],
                role=m["role"],
                content=m["content"],
                events=m["events"],
                created_at=m["created_at"],
            )
            for m in messages
        ],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str, user: User = Depends(require_user),
) -> Response:
    if not sessions_repo.delete(session_id, user.id):
        raise HTTPException(status_code=404, detail="session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages")
async def send_message(
    req: SendChatRequest, request: Request, user: User = Depends(require_user),
) -> Response:
    """Run one user turn through the chat agent, streaming JSON-RPC-like
    SSE frames back to the client.

    The agent generator (``run_chat_stream``) is sync — wrapped via
    ``iterate_in_threadpool`` so the event loop isn't blocked while
    the model produces tokens. DB writes happen via
    ``run_in_threadpool`` for the same reason.
    """
    sess = await run_in_threadpool(
        sessions_repo.get, req.session_id, user.id,
    )
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")

    # First turn? Used after the stream finishes to decide whether to
    # enqueue title generation.
    prior_count = await run_in_threadpool(
        sessions_repo.count_messages, req.session_id,
    )
    is_first_turn = prior_count == 0

    # Persist the user message before streaming. If the LLM call fails
    # halfway, the user's turn is still on the timeline.
    await run_in_threadpool(
        lambda: sessions_repo.append_message(
            req.session_id, role="user", content=req.content, events=None,
        ),
    )

    # Hydrate prior history (now including the just-saved user turn).
    history = await run_in_threadpool(
        sessions_repo.get_messages, req.session_id,
    )
    messages: list[dict[str, Any]] = messages_from_history(history)

    session_id = req.session_id
    user_id = user.id

    async def stream() -> AsyncIterator[bytes]:
        events: list[dict[str, Any]] = []
        text_parts: list[str] = []
        try:
            with trace_flow(
                "chat.send_message",
                chat_session_id=session_id,
                user_id=user_id,
                is_first_turn=is_first_turn,
            ), chat_agent_scope():
                gen = run_chat_stream(messages)
                # iterate_in_threadpool yields each item from the sync
                # generator on a worker thread, so token emission
                # doesn't block the event loop. The agent-name
                # ContextVar must be set in *this* task context
                # (chat_agent_scope above) — setting it inside the
                # generator would bind it in a per-next() copied
                # context that neither propagates to other next()
                # calls nor permits reset across them.
                async for ev in iterate_in_threadpool(gen):
                    if await request.is_disconnected():
                        break
                    events.append(ev)
                    if ev.get("type") == "text_delta":
                        text_parts.append(ev.get("text", ""))
                    yield _sse(ev)
        except LLMError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": exc.message})
            return
        except Exception:
            log.exception("chat stream failed")
            yield _sse(
                {
                    "type": "error",
                    "code": "unknown",
                    "message": "The chat agent hit an unexpected error. Check the server logs.",
                }
            )
            return

        # Stream completed cleanly — persist the assistant turn.
        try:
            await run_in_threadpool(
                lambda: sessions_repo.append_message(
                    session_id,
                    role="assistant",
                    content="".join(text_parts),
                    events=events,
                ),
            )
            await run_in_threadpool(sessions_repo.touch, session_id)
        except Exception:
            log.exception("failed to persist assistant turn session_id=%s", session_id)

        if is_first_turn:
            try:
                await run_in_threadpool(generate_chat_title, session_id)
            except Exception:
                # Title generation failures are non-fatal — log and move on.
                log.exception("failed to enqueue title task session_id=%s", session_id)

    headers = {
        "Cache-Control": "no-cache",
        # nginx hint — flush on every yield.
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        stream(), media_type="text/event-stream", headers=headers,
    )

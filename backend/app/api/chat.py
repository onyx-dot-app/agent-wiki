"""Chat endpoint backing the in-app ChatUI.

Conversations are persisted server-side. Each user owns a list of
``chat_sessions`` rows; each session holds an ordered list of
``chat_messages`` (user + assistant turns; assistant turns also carry the
full streamed event log as JSON for re-rendering).

Routes:

* ``GET    /api/chat/sessions``           — list the caller's sessions
* ``POST   /api/chat/sessions``           — create a new (untitled) session
* ``GET    /api/chat/sessions/<id>``      — session metadata + messages
* ``DELETE /api/chat/sessions/<id>``      — hard-delete the session
* ``POST   /api/chat/messages``           — send a user turn (SSE stream)

Streaming protocol — Server-Sent Events. Each event is one line of body:

    data: {"type": ..., ...}\\n\\n

Event types the frontend handles:

* ``text_delta``     — ``{text}``                assistant tokens to append
* ``tool_call``      — ``{id, name, arguments}`` agent invoked a tool
* ``tool_result``    — ``{id, name, content}``   tool returned
* ``iteration_done`` — ``{}``                    one model turn finished, agent loops
* ``done``           — ``{}``                    final assistant turn, stream closes
* ``error``          — ``{code, message}``       fatal; stream closes after this
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.auth import current_user, login_required
from app.chat import sessions as sessions_repo
from app.llm.agents.chat import run_chat_stream
from app.llm.errors import LLMError
from app.models._helpers import error, parse_body
from app.models.chat import (
    ChatMessageOut,
    ChatSessionDetail,
    ChatSessionOut,
    SendChatRequest,
)
from app.tasks.chat_title import generate_chat_title

bp = Blueprint("chat", __name__)
log = logging.getLogger(__name__)


def _session_out(row: dict[str, Any]) -> dict[str, Any]:
    return ChatSessionOut(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    ).model_dump()


# --------------------------------------------------------------------------- #
# Session CRUD                                                                #
# --------------------------------------------------------------------------- #


@bp.get("/sessions")
@login_required
def list_sessions():
    user = current_user()
    assert user is not None  # @login_required guard
    rows = sessions_repo.list_for_user(user.id)
    return jsonify([_session_out(r) for r in rows])


@bp.post("/sessions")
@login_required
def create_session():
    user = current_user()
    assert user is not None
    row = sessions_repo.create(user.id)
    return jsonify(_session_out(row)), 201


@bp.get("/sessions/<session_id>")
@login_required
def get_session(session_id: str):
    user = current_user()
    assert user is not None
    row = sessions_repo.get(session_id, user.id)
    if row is None:
        return error("session not found", 404)
    messages = sessions_repo.get_messages(session_id)
    return jsonify(
        ChatSessionDetail(
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
        ).model_dump()
    )


@bp.delete("/sessions/<session_id>")
@login_required
def delete_session(session_id: str):
    user = current_user()
    assert user is not None
    deleted = sessions_repo.delete(session_id, user.id)
    if not deleted:
        return error("session not found", 404)
    return ("", 204)


# --------------------------------------------------------------------------- #
# Streaming send                                                              #
# --------------------------------------------------------------------------- #


@bp.post("/messages")
@login_required
def send_message():
    user = current_user()
    assert user is not None

    req = parse_body(SendChatRequest, request.get_json(silent=True))
    sess = sessions_repo.get(req.session_id, user.id)
    if sess is None:
        return error("session not found", 404)

    # Was this the first user turn? Used after the stream finishes to
    # decide whether to enqueue title generation.
    prior_count = sessions_repo.count_messages(req.session_id)
    is_first_turn = prior_count == 0

    # Persist the user message before we start streaming. If the LLM call
    # fails halfway the user's message is still on the timeline.
    sessions_repo.append_message(
        req.session_id, role="user", content=req.content, events=None
    )

    # Hydrate prior history from the DB (now including the just-saved user
    # turn) into the role+content shape the agent loop expects. We don't
    # replay tool-call/tool-result blocks — the rendered text trace is
    # sufficient context for continuation.
    history = sessions_repo.get_messages(req.session_id)
    messages: list[dict[str, Any]] = [
        {"role": m["role"], "content": m["content"]} for m in history
    ]

    session_id = req.session_id

    def generate() -> Iterator[str]:
        events: list[dict[str, Any]] = []
        text_parts: list[str] = []
        try:
            for ev in run_chat_stream(messages):
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
            sessions_repo.append_message(
                session_id,
                role="assistant",
                content="".join(text_parts),
                events=events,
            )
            sessions_repo.touch(session_id)
        except Exception:
            log.exception("failed to persist assistant turn session_id=%s", session_id)

        if is_first_turn:
            try:
                generate_chat_title(session_id)
            except Exception:
                # Title generation failures are non-fatal — log and move on.
                log.exception("failed to enqueue title task session_id=%s", session_id)

    headers = {
        "Cache-Control": "no-cache",
        # Disable buffering in nginx so events flush in real time. The dev
        # proxy and prod nginx both honor this.
        "X-Accel-Buffering": "no",
    }
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers=headers,
    )


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"

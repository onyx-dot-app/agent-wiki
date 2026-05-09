"""Chat endpoint backing the in-app ChatUI.

v0 is **stateless**: the client sends the full conversation on every turn and
gets back the assistant's reply. Persistence (conversations table, history
fetch, conversation ids) is a follow-up — see `run_chat_turn` in
`app.llm.agents.chat`.

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

from flask import Blueprint, Response, request, stream_with_context

from app.auth import login_required
from app.llm.agents.chat import run_chat_stream
from app.llm.errors import LLMError
from app.models._helpers import error, parse_body
from app.models.chat import SendChatRequest

bp = Blueprint("chat", __name__)
log = logging.getLogger(__name__)


@bp.post("/messages")
@login_required
def send_message():
    req = parse_body(SendChatRequest, request.get_json(silent=True))
    if req.messages[-1].role != "user":
        return error("last message must be from the user", 400)
    messages: list[dict[str, Any]] = [m.model_dump() for m in req.messages]

    def generate() -> Iterator[str]:
        try:
            for ev in run_chat_stream(messages):
                yield _sse(ev)
        except LLMError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": exc.message})
        except Exception:
            log.exception("chat stream failed")
            yield _sse(
                {
                    "type": "error",
                    "code": "unknown",
                    "message": "The chat agent hit an unexpected error. Check the server logs.",
                }
            )

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

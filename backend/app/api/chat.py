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
from typing import Iterator

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.auth import login_required
from app.llm.agents.chat import run_chat_stream
from app.llm.client import LLMError

bp = Blueprint("chat", __name__)
log = logging.getLogger(__name__)

_ALLOWED_ROLES = {"user", "assistant"}


@bp.post("/messages")
@login_required
def send_message():
    body = request.get_json(silent=True) or {}
    raw = body.get("messages")
    if not isinstance(raw, list) or not raw:
        return jsonify(error="messages must be a non-empty list"), 400

    messages: list[dict] = []
    for m in raw:
        if (
            not isinstance(m, dict)
            or m.get("role") not in _ALLOWED_ROLES
            or not isinstance(m.get("content"), str)
        ):
            return jsonify(error="each message needs role (user|assistant) and string content"), 400
        messages.append({"role": m["role"], "content": m["content"]})

    if messages[-1]["role"] != "user":
        return jsonify(error="last message must be from the user"), 400

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


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"

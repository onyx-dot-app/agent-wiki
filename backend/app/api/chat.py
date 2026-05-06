"""Chat endpoint backing the in-app ChatUI.

The chat agent can read the wiki via search, propose doc edits, and manage the
current user's triggers. Prefer a multi-iteration loop (see
``app/llm/agents/chat.py``).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth import login_required

bp = Blueprint("chat", __name__)


@bp.post("/messages")
@login_required
def send_message():
    # body: {conversation_id?, message}
    # TODO: run the chat agent loop (search wiki, edit docs, manage triggers).
    raise NotImplementedError


@bp.get("/conversations")
@login_required
def list_conversations():
    raise NotImplementedError


@bp.get("/conversations/<conv_id>")
@login_required
def get_conversation(conv_id: str):
    raise NotImplementedError

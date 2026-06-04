"""HTTP shapes for /api/chat."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SendChatRequest(BaseModel):
    """Body for ``POST /api/chat/messages``.

    The session must already exist (the frontend creates it via
    ``POST /api/chat/sessions`` on first send). The backend loads the
    full prior message history from the DB and runs the agent loop —
    only the latest user content travels over the wire.
    """

    session_id: str
    content: str = Field(min_length=1)


class DraftingInitRequest(BaseModel):
    """Body for ``POST /api/chat/drafting/init``.

    Creates a fresh hidden chat session seeded with a synthetic user
    message (also hidden) that primes the agent. The user never sees
    the seed turn; they just see the assistant respond with guiding
    questions about filling out the page.

    ``template_id`` is optional — when omitted (or null), the seed is a
    generic "what do you want to work on" prime that also hints at the
    wiki's auto-fill behavior. Used by the "Blank document" picker
    option, which has no template to attach to.
    """

    template_id: str | None = Field(default=None, min_length=1)
    # The "Start writing with AI" prompt. When set, it becomes a VISIBLE first
    # user turn (a draft was already generated into the editor for it) instead
    # of the generic blank prime.
    prompt: str | None = Field(default=None, min_length=1)


class ChatSessionOut(BaseModel):
    id: str
    title: str | None
    created_at: str
    updated_at: str


class ChatMessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    events: list[dict[str, Any]] | None
    created_at: str


class ChatSessionDetail(BaseModel):
    session: ChatSessionOut
    messages: list[ChatMessageOut]

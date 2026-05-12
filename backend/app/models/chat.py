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
    message (also hidden) that primes the agent with the chosen
    template's body + optional system prompt. Streams the agent's
    kickoff response. The user never sees the seed turn; they just see
    the assistant respond with guiding questions about filling out the
    page.
    """

    template_id: str = Field(min_length=1)


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

"""HTTP shapes for /api/chat."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SendChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)

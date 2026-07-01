"""HTTP request/response shapes for the co-editing live channel.

Kept separate from the ORM rows in ``app/wiki/coedit.py`` (the DB shape) and the
in-memory frames in ``app/wiki/coedit_channel.py`` (the wire shape).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.wiki.coedit import Change


class JoinRequest(BaseModel):
    path: str


class LeaveRequest(BaseModel):
    session_id: int


class OpRequest(BaseModel):
    session_id: int
    base_version: int
    # Range changes ({from,to,insert}); FastAPI validates each via Change, so a
    # malformed op is a 400 (the app's RequestValidationError handler) before it
    # reaches the repo.
    changes: list[Change]


class OpResponse(BaseModel):
    version: int  # the session version this op produced


class CursorRequest(BaseModel):
    """A participant's live cursor/selection. ``anchor``/``head`` are UTF-16
    offsets (like Change); a collapsed selection (anchor == head) is a caret,
    otherwise it's a highlighted range. Ephemeral — never persisted."""

    session_id: int
    anchor: int = Field(ge=0)
    head: int = Field(ge=0)
    typing: bool = False


class ParticipantOut(BaseModel):
    user_id: str
    user_display: str
    joined_at: str
    last_seen_at: str


class JoinResponse(BaseModel):
    session_id: int
    buffer: str
    version: int
    base_sha: str | None
    participants: list[ParticipantOut]

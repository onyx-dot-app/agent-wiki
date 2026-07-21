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


class CheckpointRequest(BaseModel):
    session_id: int


class OpRequest(BaseModel):
    session_id: int
    base_version: int
    # Range changes ({from,to,insert}); FastAPI validates each via Change, so a
    # malformed op is a 400 (the app's RequestValidationError handler) before it
    # reaches the repo.
    changes: list[Change]
    # Opaque per-connection id (one editor tab). Lets a collaborative client
    # recognize its own op when it echoes back. Optional — non-collab clients
    # omit it. Bounded: it's a UUID/tab id, and it's persisted + echoed to every
    # participant, so cap it to keep a client from bloating the log / bus.
    client_id: str | None = Field(default=None, max_length=256)
    # The sender's caret epoch while their caret is placed — echoed into the
    # op frame so peers render the author's caret at the edit (and drop it if
    # a newer clear was already seen). Omitted when the sender's caret is
    # cleared (e.g. a teardown flush after blur); the frame then carries no
    # caret assertion. Never persisted.
    caret_seq: int | None = Field(default=None, ge=0)


class OpResponse(BaseModel):
    version: int  # the session version this op produced


class CursorRequest(BaseModel):
    """A participant's live cursor/selection. ``anchor``/``head`` are UTF-16
    offsets (like Change); a collapsed selection (anchor == head) is a caret,
    otherwise it's a highlighted range. ``None`` (either offset omitted) means
    the caller *cleared* their caret — the editor lost focus or the tab was
    hidden — so peers drop the caret and presence flips them to "viewing".
    Ephemeral end to end: broadcast to the session, never persisted."""

    session_id: int
    anchor: int | None = Field(default=None, ge=0)
    head: int | None = Field(default=None, ge=0)
    typing: bool = False
    # Client caret epoch: bumped on every place/clear transition, echoed
    # unchanged by movement pings. Rides the frame so peers drop reordered
    # stale frames (a place broadcast landing after a newer clear must not
    # resurrect the caret). None = a client predating the epoch protocol.
    seq: int | None = Field(default=None, ge=0)


class ParticipantOut(BaseModel):
    user_id: str
    user_display: str
    joined_at: str
    last_seen_at: str
    # None until the participant applies an edit op. Presence does not read
    # this — the "editing"/"viewing" label derives client-side from the live
    # caret frames.
    last_edited_at: str | None = None


class JoinResponse(BaseModel):
    session_id: int
    buffer: str
    version: int
    base_sha: str | None
    participants: list[ParticipantOut]


class Operation(BaseModel):
    """One logged edit operation (one version bump), wire-shaped like the SSE
    ``op`` frame so a client can feed it to the same apply/rebase path. Its
    ``changes`` are the range edits applied together in that operation (≥1)."""

    version: int  # the new buffer version after this operation applies (coedit_ops.seq)
    author: str  # author_user_id
    client_id: str | None  # originating connection (collab); None for non-collab ops
    changes: list[Change]


class OpsResponse(BaseModel):
    """Ops after a given version (oldest first) + the current head version, so a
    client can rebase its unconfirmed edits after a 409 / reconnect / big-op
    resync instead of replacing the buffer wholesale."""

    session_id: int
    current_head_version: int
    ops: list[Operation]

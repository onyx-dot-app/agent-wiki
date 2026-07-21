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
    # The sender's current caret epoch while their caret is placed — an edit
    # asserts caret placement at that epoch (guarded like a cursor write, so a
    # reordered op can't resurrect a later-cleared caret). Omitted when the
    # sender's caret is cleared (e.g. a teardown flush after blur) or by
    # clients predating the epoch protocol; the op then leaves caret state
    # untouched.
    caret_seq: int | None = Field(default=None, ge=0)


class OpResponse(BaseModel):
    version: int  # the session version this op produced


class CursorRequest(BaseModel):
    """A participant's live cursor/selection. ``anchor``/``head`` are UTF-16
    offsets (like Change); a collapsed selection (anchor == head) is a caret,
    otherwise it's a highlighted range. ``None`` (either offset omitted) means
    the caller *cleared* their caret — the editor lost focus or the tab was
    hidden — so peers drop the caret and presence flips them to "viewing".
    Positions are ephemeral (broadcast, never persisted); only the on/off
    caret state is stamped on the participant row."""

    session_id: int
    anchor: int | None = Field(default=None, ge=0)
    head: int | None = Field(default=None, ge=0)
    typing: bool = False
    # Client caret epoch: bumped on every place/clear transition, echoed
    # unchanged by movement pings. Orders concurrent caret writes server-side
    # (older epochs lose) and lets peers drop stale frames. None = a client
    # predating the epoch protocol (state changes apply last-writer-wins).
    seq: int | None = Field(default=None, ge=0)


class ParticipantOut(BaseModel):
    user_id: str
    user_display: str
    joined_at: str
    last_seen_at: str
    # None until the participant applies an edit op.
    last_edited_at: str | None = None
    # True while the participant has a caret placed in the text — presence
    # renders them "editing" rather than "viewing". ``caret_seq`` is the caret
    # epoch that ordered the last applied caret write; clients seed their
    # stale-frame guard (and a rejoining editor its own epoch counter) from it.
    caret_active: bool = False
    caret_seq: int = 0


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

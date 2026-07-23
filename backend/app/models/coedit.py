"""Wire shapes for the co-editing live channel — one ``WebSocket`` per
session (``app/api/coedit.py``), messages multiplexed by a ``type`` field.

Kept separate from the ORM rows in ``app/wiki/coedit.py`` (the DB shape) and the
in-memory frames in ``app/wiki/coedit_channel.py`` (the pub/sub wire shape).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.wiki.coedit import Change


class OpMessage(BaseModel):
    """Client -> server ``{"type": "op", ...}``. No ``session_id`` — the
    connection itself is scoped to one session (see ``JoinFrame``)."""

    request_id: str
    base_version: int
    # Range changes ({from,to,insert}); pydantic validates each via Change, so
    # a malformed op gets an error reply before it reaches the repo.
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


class OpResultFrame(BaseModel):
    """Server -> client direct reply to an ``OpMessage``, correlated by
    ``request_id``. Not a broadcast frame — only the sender gets this."""

    type: str = "op_result"
    request_id: str
    ok: bool
    version: int | None = None  # set when ok
    error: str | None = None  # "stale_version" | "invalid_op" | "forbidden"


class CursorMessage(BaseModel):
    """Client -> server ``{"type": "cursor", ...}``, fire-and-forget (no
    ``request_id`` — matches today's swallowed-error handling on the HTTP
    path). ``anchor``/``head`` are UTF-16 offsets (like ``Change``); a
    collapsed selection (anchor == head) is a caret, otherwise a highlighted
    range. ``None`` (either offset omitted) means the caller *cleared* their
    caret — peers drop it and presence flips them to "viewing". Ephemeral end
    to end: broadcast to the session, never persisted."""

    anchor: int | None = Field(default=None, ge=0)
    head: int | None = Field(default=None, ge=0)
    typing: bool = False
    # Client caret epoch: bumped on every place/clear transition, echoed
    # unchanged by movement pings. Rides the frame so peers drop reordered
    # stale frames (a place broadcast landing after a newer clear must not
    # resurrect the caret). None = a client predating the epoch protocol.
    seq: int | None = Field(default=None, ge=0)


class CheckpointMessage(BaseModel):
    """Client -> server ``{"type": "checkpoint", ...}``."""

    request_id: str


class CheckpointResultFrame(BaseModel):
    type: str = "checkpoint_result"
    request_id: str
    ok: bool


class GetOpsMessage(BaseModel):
    """Client -> server ``{"type": "get_ops", ...}`` — replaces the old
    ``GET /coedit/ops`` catch-up-after-a-gap read."""

    request_id: str
    since_version: int


class ParticipantOut(BaseModel):
    user_id: str
    user_display: str
    joined_at: str
    last_seen_at: str
    # None until the participant applies an edit op. Presence does not read
    # this — the "editing"/"viewing" label derives client-side from the live
    # caret frames.
    last_edited_at: str | None = None


class JoinedFrame(BaseModel):
    """Server -> client, sent once right after ``accept()`` — the WS
    connection's own handshake response, replacing the old ``POST /join``
    response body."""

    type: str = "joined"
    session_id: int
    buffer: str
    version: int
    base_sha: str | None
    participants: list[ParticipantOut]


class Operation(BaseModel):
    """One logged edit operation (one version bump), wire-shaped like the
    broadcast ``op`` frame so a client can feed it to the same apply/rebase
    path. Its ``changes`` are the range edits applied together in that
    operation (≥1)."""

    version: int  # the new buffer version after this operation applies (coedit_ops.seq)
    author: str  # author_user_id
    client_id: str | None  # originating connection (collab); None for non-collab ops
    changes: list[Change]


class OpsResultFrame(BaseModel):
    """Server -> client reply to a ``GetOpsMessage``, correlated by
    ``request_id``. Ops after a given version (oldest first) + the current
    head version, so a client can rebase its unconfirmed edits after a
    stale-version reject / reconnect / big-op resync instead of replacing the
    buffer wholesale. ``ok``/``error`` mirror the other result frames — a
    mid-session read-permission loss (rare) still gets a reply rather than
    leaving the caller's correlated promise hanging forever."""

    type: str = "ops_result"
    request_id: str
    ok: bool = True
    error: str | None = None  # "no_active_session" | "forbidden"
    current_head_version: int
    ops: list[Operation]

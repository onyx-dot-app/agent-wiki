"""Wire shapes for the co-editing live channel — one ``WebSocket`` per
session (``app/api/coedit.py``).

The document itself travels as raw Yjs sync/awareness protocol bytes (WS
binary frames, handled directly against ``pycrdt`` — see
``app/wiki/coedit_room.py``), never as JSON. These models are only the
small set of JSON control messages (WS text frames) that ride alongside:
explicit checkpoint requests and the connection handshake. Kept separate
from the ORM rows in ``app/wiki/coedit.py`` (the DB shape) and the in-memory
frames in ``app/wiki/coedit_channel.py`` (the pub/sub wire shape).
"""

from __future__ import annotations

from pydantic import BaseModel


class CheckpointMessage(BaseModel):
    """Client -> server ``{"type": "checkpoint", ...}``."""

    request_id: str


class CheckpointResultFrame(BaseModel):
    type: str = "checkpoint_result"
    request_id: str
    ok: bool
    error: str | None = None  # "no_active_session" | "forbidden"


class ParticipantOut(BaseModel):
    user_id: str
    user_display: str
    joined_at: str
    last_seen_at: str
    # None until the participant applies an edit. Presence does not read
    # this — the "editing"/"viewing" label derives client-side from
    # Awareness cursor state.
    last_edited_at: str | None = None


class JoinErrorFrame(BaseModel):
    """Server -> client, sent (once ``accept()``ed) *instead of*
    ``JoinedFrame`` when the join can't complete for a reason retrying
    won't fix — today, a page containing a construct the live-editor codec
    can't encode (see ``markdown_yjs.py``'s module docstring). Sent after
    ``accept()``, not before, specifically so the browser's ``WebSocket``
    API can actually see it: a pre-upgrade HTTP-level rejection carries no
    status/body a real browser's ``onclose``/``onerror`` can read (unlike
    the test client's ``WebSocketDenialResponse``), so a page-content
    failure has to ride a real frame instead. The connection closes right
    after this frame."""

    type: str = "join_error"
    detail: str


class JoinedFrame(BaseModel):
    """Server -> client, sent once right after ``accept()`` — the WS
    connection's own handshake response. Carries no document content (that
    travels over the binary Yjs sync handshake the server initiates
    immediately after this frame, see ``app/api/coedit.py:ws``)."""

    type: str = "joined"
    session_id: int
    base_sha: str | None
    can_write: bool
    participants: list[ParticipantOut]


class PresenceFrame(BaseModel):
    """Server -> client, broadcast whenever the participant roster changes
    (join/leave) — distinct from Yjs Awareness (live cursor/color state);
    this is the durable, DB-backed roster, which includes pure viewers who
    may never touch Awareness at all."""

    type: str = "presence"
    session_id: int
    participants: list[ParticipantOut]


class ResyncFrame(BaseModel):
    """Server -> client: the live document was replaced wholesale (a
    live-rebase folding in an out-of-band commit, see
    ``app/wiki/coedit_rebase.py``) rather than incrementally updated. The
    client's local Yjs state no longer has a valid incremental path to the
    new server state, so it must reconnect (close and reopen the
    WebSocket) to redo the sync handshake fresh, rather than attempt to
    reconcile in place."""

    type: str = "resync"
    session_id: int

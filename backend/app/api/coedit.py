"""The page live-session channel — SSE down, HTTP POST up (cookie-authed humans).

A "co-edit session" is the page's *live session*: everyone viewing the page
joins it (read-gated — presence + real-time updates), and editing is a
capability inside it (`/op`/`/cursor` are write-gated; `caret_active` — whether
a caret is placed in the text — separates editors from viewers in presence).

Thin HTTP layer: gate by page permission, drive the ``app/wiki/coedit.py`` store
and the ``app/wiki/coedit_channel.py`` broadcast layer. The SSE stream is a
plain sync generator, one threadpool thread per open connection.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.auth import User, require_can
from app.auth.deps import require_user
from app.models.coedit import (
    CheckpointRequest,
    CursorRequest,
    JoinRequest,
    JoinResponse,
    LeaveRequest,
    OpRequest,
    OpResponse,
    OpsResponse,
    Operation,
    ParticipantOut,
)
from app.tasks.coedit_checkpoint import checkpoint_coedit_session
from app.wiki import coedit, coedit_channel, git

router = APIRouter()
log = logging.getLogger(__name__)

# Matches the MCP SSE cadence so proxies see the same idle behavior.
_SSE_HEARTBEAT_SECONDS = 15.0


def _participants_out(session_id: int) -> list[ParticipantOut]:
    return [
        ParticipantOut(
            user_id=p.user_id,
            user_display=p.user_display,
            joined_at=p.joined_at,
            last_seen_at=p.last_seen_at,
            last_edited_at=p.last_edited_at,
            caret_active=p.caret_active,
        )
        for p in coedit.list_participants(session_id)
    ]


@router.post("/join")
def join(req: JoinRequest, user: User = Depends(require_user)) -> JoinResponse:
    """Open (or join) the live session for a page and return its buffer.

    Joining is opening the page — presence + the live op stream — so it
    requires only read; *writing* is gated at ``/op``/``/cursor``. A
    participant who never places a caret shows as "viewing" in presence
    (``caret_active`` stays false). A fresh session is seeded from the
    page's current HEAD; an already-open session is adopted as-is (its live
    buffer wins).
    """
    require_can("read", req.path, user)
    head = git.head_sha_for_path(req.path)
    initial = git.read_file_opt(req.path) or ""
    sess = coedit.open_session(req.path, base_sha=head, initial_buffer=initial)
    coedit.join(sess.id, user.id)
    coedit_channel.broadcast_presence(sess.id)
    return JoinResponse(
        session_id=sess.id,
        buffer=sess.buffer_text,
        version=sess.version,
        base_sha=sess.base_sha,
        participants=_participants_out(sess.id),
    )


def _checkpoint_if_last_left(session_id: int) -> None:
    """Enqueue a final checkpoint when the last participant has left, so the
    session's buffer lands in git without waiting for the periodic scan.

    Best-effort: the participant row is already gone, so a failed enqueue (e.g.
    a full queue) must not fail the leave. The periodic scan is the backstop —
    the session is dirty, so it's recovered (checkpointed + closed) once idle."""
    if coedit.list_participants(session_id):
        return
    try:
        checkpoint_coedit_session(session_id)
    except Exception:
        log.exception("coedit: checkpoint enqueue failed on last-leave for session %s", session_id)


@router.post("/leave")
def leave(req: LeaveRequest, user: User = Depends(require_user)) -> dict[str, bool]:
    """Explicitly leave a session (e.g. closing the editor)."""
    coedit.leave(req.session_id, user.id)
    coedit_channel.broadcast_presence(req.session_id)
    _checkpoint_if_last_left(req.session_id)
    return {"ok": True}


def _require_active(session_id: int, user: User, action: str) -> coedit.SessionRow:
    sess = coedit.get_session(session_id)
    if sess is None or sess.status != coedit.SessionStatus.ACTIVE.value:
        raise HTTPException(status_code=404, detail="no active session")
    require_can(action, sess.path, user)
    return sess


@router.post("/op")
def op(req: OpRequest, user: User = Depends(require_user)) -> OpResponse:
    """Apply an edit op to the session buffer and broadcast it.

    409 if ``base_version`` is stale (the client re-syncs via GET /session and
    re-applies); 422 if the op is out of bounds / overlapping.
    """
    _require_active(req.session_id, user, "write")
    try:
        out = coedit.apply_op(
            req.session_id,
            base_version=req.base_version,
            changes=req.changes,
            author_user_id=user.id,
            client_id=req.client_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if out is None:
        raise HTTPException(status_code=409, detail="stale base_version; re-sync and retry")
    coedit.touch(req.session_id, user.id, edited=True)
    coedit_channel.broadcast_op(
        req.session_id, out.version, req.changes, user.id, client_id=req.client_id
    )
    return OpResponse(version=out.version)


@router.post("/cursor")
def cursor(req: CursorRequest, user: User = Depends(require_user)) -> dict[str, bool]:
    """Broadcast the caller's live cursor/selection to the session.

    A null anchor/head clears the caret (the editor lost focus / the tab went
    hidden) — peers drop it and presence flips the sender to "viewing".
    High-frequency + throttled client-side; positions stay ephemeral (no
    last_seen write — the SSE heartbeat covers liveness), only the on/off
    caret state lands in the DB, and only on a transition, so the roster is
    right for late joiners.
    """
    _require_active(req.session_id, user, "write")
    cleared = req.anchor is None or req.head is None
    coedit.set_caret_active(req.session_id, user.id, active=not cleared)
    coedit_channel.broadcast_cursor(
        req.session_id,
        user_id=user.id,
        # Match list_participants' SQL COALESCE(name, email) exactly — substitute
        # only on NULL, not on "" — so a peer's caret label and roster name agree.
        user_display=user.name if user.name is not None else user.email,
        anchor=None if cleared else req.anchor,
        head=None if cleared else req.head,
        typing=req.typing and not cleared,
    )
    return {"ok": True}


@router.post("/checkpoint")
def checkpoint(req: CheckpointRequest, user: User = Depends(require_user)) -> dict[str, bool]:
    """Explicit save: enqueue a checkpoint of the session's buffer to git.

    Async (the commit + any merge run on the worker), so this returns once
    queued rather than blocking on the git write.
    """
    _require_active(req.session_id, user, "write")
    checkpoint_coedit_session(req.session_id)
    return {"queued": True}


@router.get("/session")
def session_state(session_id: int, user: User = Depends(require_user)) -> JoinResponse:
    """Current buffer + version + roster — a read-only snapshot for a client to
    re-sync after a stale op or a `resync` frame (no join side effects)."""
    sess = _require_active(session_id, user, "read")
    return JoinResponse(
        session_id=sess.id,
        buffer=sess.buffer_text,
        version=sess.version,
        base_sha=sess.base_sha,
        participants=_participants_out(sess.id),
    )


@router.get("/ops")
def ops(
    session_id: int, since_version: int, user: User = Depends(require_user)
) -> OpsResponse:
    """Ops applied after ``since_version`` (oldest first) + the current head
    version. Lets a client rebase its unconfirmed edits after a stale op (409),
    a reconnect, or a big-op ``resync`` — replaying the exact missed changes
    rather than replacing the buffer. Read-only; no join side effects."""
    sess = _require_active(session_id, user, "read")
    # Head version + ops read as one consistent snapshot (see
    # ops_since_with_head), so a concurrent op can't desync them.
    result = coedit.ops_since_with_head(session_id, since_version)
    return OpsResponse(
        session_id=session_id,
        current_head_version=(
            result.head_version if result.head_version is not None else sess.version
        ),
        ops=[
            Operation(
                version=r.seq,
                author=r.author_user_id,
                client_id=r.client_id,
                changes=[coedit.Change.model_validate(c) for c in r.changes],
            )
            for r in result.ops
        ],
    )


@router.get("/stream")
def stream(session_id: int, user: User = Depends(require_user)) -> StreamingResponse:
    """Long-lived SSE stream of session frames (presence).

    A plain sync generator: it blocks on the connection's queue, emitting a
    keepalive every ``_SSE_HEARTBEAT_SECONDS`` of silence. The connection is the
    presence heartbeat — each keepalive refreshes ``last_seen_at``. When the
    client disconnects, Starlette closes the generator (``GeneratorExit`` at the
    next yield, at most one heartbeat later), and the ``finally`` block fires
    ``leave`` once the user's last connection for the session closes.
    """
    sess = coedit.get_session(session_id)
    if sess is None or sess.status != coedit.SessionStatus.ACTIVE.value:
        raise HTTPException(status_code=404, detail="no active session")
    # Opening the stream makes the user a session participant (roster +
    # heartbeat), which is page-open presence, not edit intent — so it
    # requires read, symmetric with POST /join. Writes stay gated at /op.
    require_can("read", sess.path, user)

    coedit.join(session_id, user.id)
    # Announce the new participant to existing connections *before* registering
    # this one — otherwise the broadcast lands in our own queue and gen() would
    # emit it on top of the inline initial roster below (a duplicate frame).
    coedit_channel.broadcast_presence(session_id)
    conn = coedit_channel.connect(session_id, user.id)

    def gen() -> Iterator[bytes]:
        try:
            # Prime the stream with the current roster so a joiner doesn't wait
            # for the next change to render presence.
            yield _sse(
                {
                    "type": "presence",
                    "session_id": session_id,
                    "participants": [
                        p.model_dump() for p in coedit.list_participants(session_id)
                    ],
                }
            )
            while True:
                frame = coedit_channel.drain(conn.queue, _SSE_HEARTBEAT_SECONDS)
                if frame is None:
                    coedit.touch(session_id, user.id)
                    yield b": keepalive\n\n"
                    continue
                yield _sse(frame)
        finally:
            coedit_channel.disconnect(conn.id)
            # Only mark the user gone when their last connection closes, so a
            # second tab doesn't evict them.
            if not coedit_channel.user_still_connected(session_id, user.id):
                coedit.leave(session_id, user.id)
                coedit_channel.broadcast_presence(session_id)
                _checkpoint_if_last_left(session_id)
            log.info("coedit sse closed session=%s user=%s", session_id, user.id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _sse(frame: dict[str, object]) -> bytes:
    return f"data: {json.dumps(frame)}\n\n".encode("utf-8")

"""Co-editing live channel — SSE down, HTTP POST up (cookie-authed humans).

Thin HTTP layer: gate by page permission, drive the ``app/wiki/coedit.py`` store
and the ``app/wiki/coedit_channel.py`` broadcast layer. Edit ops land in a
follow-up (build-order step 3); this PR establishes the channel + presence.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth import User, require_can
from app.auth.deps import require_user
from app.models.coedit import (
    JoinRequest,
    JoinResponse,
    LeaveRequest,
    ParticipantOut,
)
from app.wiki import coedit, coedit_channel
from app.wiki import git

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
        )
        for p in coedit.list_participants(session_id)
    ]


@router.post("/join")
async def join(req: JoinRequest, user: User = Depends(require_user)) -> JoinResponse:
    """Open (or join) the live session for a page and return its buffer.

    Joining is editing, so it requires write. A fresh session is seeded from the
    page's current HEAD; an already-open session is adopted as-is (its live
    buffer wins).
    """
    require_can("write", req.path, user)
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


@router.post("/leave")
async def leave(req: LeaveRequest, user: User = Depends(require_user)) -> dict[str, bool]:
    """Explicitly leave a session (e.g. closing the editor)."""
    coedit.leave(req.session_id, user.id)
    coedit_channel.broadcast_presence(req.session_id)
    return {"ok": True}


@router.get("/stream")
async def stream(
    session_id: int,
    request: Request,
    user: User = Depends(require_user),
) -> StreamingResponse:
    """Long-lived SSE stream of session frames (presence now, ops later).

    The connection is the presence heartbeat: each keepalive tick refreshes the
    participant's ``last_seen_at``, and on disconnect we fire ``leave`` once the
    user's last connection for the session closes.
    """
    sess = coedit.get_session(session_id)
    if sess is None or sess.status != "active":
        raise HTTPException(status_code=404, detail="no active session")
    # Still need read access to the underlying page to watch it being edited.
    require_can("read", sess.path, user)

    coedit.join(session_id, user.id)
    conn_id, queue = coedit_channel.connect(session_id, user.id)
    coedit_channel.broadcast_presence(session_id)

    async def gen() -> AsyncIterator[bytes]:
        try:
            # Prime the stream with the current roster so a joiner doesn't wait
            # for the next change to render presence.
            yield _sse({"type": "presence", "session_id": session_id,
                        "participants": [p.model_dump() for p in coedit.list_participants(session_id)]})
            while True:
                if await request.is_disconnected():
                    break
                frame = await coedit_channel.drain(queue, _SSE_HEARTBEAT_SECONDS)
                if frame is None:
                    coedit.touch(session_id, user.id)
                    yield b": keepalive\n\n"
                    continue
                yield _sse(frame)
        except asyncio.CancelledError:
            raise
        finally:
            coedit_channel.disconnect(conn_id)
            # Only mark the user gone when their last connection closes, so a
            # second tab doesn't evict them.
            if not coedit_channel.user_still_connected(session_id, user.id):
                coedit.leave(session_id, user.id)
                coedit_channel.broadcast_presence(session_id)
            log.info("coedit sse closed session=%s user=%s", session_id, user.id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _sse(frame: dict[str, object]) -> bytes:
    return f"data: {json.dumps(frame)}\n\n".encode("utf-8")

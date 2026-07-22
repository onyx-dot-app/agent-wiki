"""WebSocket transport for the onyx-editor live doc (Phase 1,
``plans/onyx-editor.md``) — a new, additive endpoint alongside
``app/api/coedit.py``'s SSE+POST protocol. See ``app/wiki/coedit_ws.py`` for
the room registry this thin route drives.

Permission gating: the design doc calls for read at connect, write at
update-apply. Validating this against ``pycrdt.websocket``'s actual room
abstraction (not assumed) surfaced that its ``YRoom``/``WebsocketServer``
treat every connected peer symmetrically for applying updates — there is no
per-connection hook to accept one client's writes while relaying another's
read-only. Filtering per-message would mean hand-parsing the Yjs sync
protocol's message framing to distinguish an update-apply from a harmless
awareness frame, which is real scope beyond Phase 1. So for now: gate once,
at connect, on *write* — a read-only viewer is refused this endpoint
entirely and falls back to the existing ``/join`` + ``/stream`` (read-gated)
path, which already serves exactly that use case. True mixed-permission
viewing over this transport is an open item for Phase 2/3, not resolved
here.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pycrdt.websocket.websocket import HttpxWebsocket
from pycrdt.websocket.yroom import YRoom

from app.auth import PermissionDenied, User, require_can
from app.auth.deps import require_user_ws
from app.wiki import coedit, coedit_ws, git as wiki_git
from app.wiki.coedit_checkpoint import checkpoint_ydoc_session
from app.wiki.markdown_splice import TouchedTracker

router = APIRouter()
log = logging.getLogger(__name__)


@router.websocket("/ws/{path:path}")
async def ws(websocket: WebSocket, path: str, user: User = Depends(require_user_ws)) -> None:
    """``path`` is a URL path segment, not a query param — matching
    ``y-websocket``'s client provider, which builds its connection URL as
    ``serverUrl/roomname`` (verified directly against its source: no
    built-in way to hand it a query-param-based room address without a
    custom provider). ``:path`` (not a plain ``{path}``) so a wiki path
    containing ``/`` (e.g. ``guides/setup.md``) survives as one segment
    rather than 404ing on the extra slash.
    """
    try:
        require_can("read", path, user)
        require_can("write", path, user)
    except PermissionDenied:
        await websocket.close(code=1008, reason="write permission required")
        return

    sess = coedit.open_session(path, base_sha=wiki_git.head_sha_for_path(path), initial_buffer="")
    coedit.join(sess.id, user.id)
    # Capture the room/tracker now, before serve() runs — WebsocketServer's
    # auto_clean_rooms pops the room out of SERVER.rooms itself the moment
    # the last client disconnects, *inside* the serve() call below, which
    # would otherwise race our own last-leave checkpoint's lookup below.
    room = await coedit_ws.get_or_create_room(path, session_id=sess.id)
    tracker = coedit_ws.tracker_for(path)

    await websocket.accept()
    channel = HttpxWebsocket(websocket, path)
    try:
        await coedit_ws.SERVER.serve(channel)
    except WebSocketDisconnect:
        pass
    finally:
        coedit.leave(sess.id, user.id)
        if not coedit.list_participants(sess.id):
            # Last participant left this process's connection to the room —
            # checkpoint now rather than waiting for the next scan tick.
            # Mirrors _checkpoint_if_last_left in api/coedit.py, but must run
            # in-process (see coedit_checkpoint's module note: the live doc
            # only exists here, not on a coedit_queue worker).
            #
            # Fired as an independent task, not awaited inline: once the
            # client disconnects, the ASGI layer tears down *this*
            # coroutine's task promptly (observed directly — an inline
            # `await checkpoint_ydoc_session(...)` here gets cut off
            # mid-checkpoint), so anything that needs to actually finish
            # (the git commit) has to run outside that lifecycle.
            coedit_ws.drop_room(path)
            if tracker is not None and room is not None:
                asyncio.create_task(_finish_checkpoint(sess.id, room, tracker, user.id))
            else:
                coedit.close_if_clean(sess.id)


async def _finish_checkpoint(
    session_id: int, room: YRoom, tracker: TouchedTracker, author_user_id: str
) -> None:
    """Runs as an independent task past the WS connection's own lifecycle —
    see the comment at its call site in ``ws`` above."""
    try:
        await checkpoint_ydoc_session(
            session_id, doc=room.ydoc, tracker=tracker, author_user_id=author_user_id
        )
    except Exception:
        log.exception("coedit_ws: checkpoint failed on last-leave for session %s", session_id)
    coedit.close_if_clean(session_id)

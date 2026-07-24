"""The co-edit live-doc WebSocket — one connection per participant, relaying
the raw Yjs sync protocol between the client's ``y-websocket`` provider and
this process's in-memory room (``app/wiki/coedit_ws.py``). Unlike a typed
JSON-message endpoint, there's no ``{"type": ...}`` dispatch here — the wire
protocol is opaque Yjs sync-protocol bytes, and ``pycrdt.websocket``'s
``YRoom.serve()`` owns the actual sync-step/update/awareness handling.

The first ``asyncio`` in this backend's WS routes still applies: ``async``
here covers connection lifecycle only (accept, permission gating, the
leave/checkpoint teardown); every blocking domain call
(``require_can``/``coedit.*``/git reads) runs via ``asyncio.to_thread``. See
CLAUDE.md's "WebSocket routes" rule.

Permission gating is two-tier, not a single connect-time check:

- **Connect time**: ``require_can("read", path, user)`` only — a read-only
  viewer is welcome on this endpoint (they just never send a write-carrying
  frame; the room still shows them everyone else's live content).
- **Per update**: ``_PermissionCheckedChannel`` (below) inspects every
  inbound frame's Yjs message-type byte *before* it ever reaches
  ``room.serve()``, and re-checks ``require_can("write", path, user)``
  specifically on frames that can mutate the shared doc (a sync-step2 reply
  carrying content, a sync-update, or an awareness/cursor update — matching
  the OT-era route's ``_handle_cursor`` also being write-gated). A plain
  sync-step1 (a bare state-vector request, no payload) is exempt — it can't
  mutate anything.

This is the per-message write-permission re-check PR #489 established as a
hard invariant for any WS-based coedit transport, applied here for the first
time to a Yjs relay. ``YRoom.on_message`` (a documented hook) was considered
and rejected: it's scoped to the *room*, shared across every simultaneously
connected client, with no way to tell which connection a given message came
from — it can enforce a uniform room-wide policy, not a per-sender one. The
``Channel`` object (``pycrdt.Channel`` — ``path``/``send``/``recv``), by
contrast, genuinely is one-per-connection, which is why the check is done by
wrapping it instead.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pycrdt import Channel, YMessageType, YSyncMessageType
from pycrdt.websocket.websocket import HttpxWebsocket
from pycrdt.websocket.yroom import YRoom

from app.auth import PermissionDenied, User, require_can
from app.auth.deps import require_user_ws
from app.tasks.coedit_leave import leave_coedit_session, record_leave
from app.wiki import coedit, coedit_ws
from app.wiki import git as wiki_git
from app.wiki.coedit_checkpoint import checkpoint_ydoc_session
from app.wiki.markdown_splice import TouchedTracker

router = APIRouter()
log = logging.getLogger(__name__)


def _carries_write(message: bytes) -> bool:
    """True if ``message`` is a Yjs sync-protocol frame that can mutate the
    shared doc: an awareness update (cursor/presence — write-gated to match
    the OT-era route's ``_handle_cursor``), or a sync message carrying an
    actual payload (SYNC_STEP2's reply content, or a SYNC_UPDATE). A bare
    SYNC_STEP1 (a state-vector request with no payload) never mutates
    anything and is exempt."""
    if not message:
        return False
    if message[0] == YMessageType.AWARENESS:
        return True
    if message[0] == YMessageType.SYNC and len(message) >= 2:
        return message[1] in (YSyncMessageType.SYNC_STEP2, YSyncMessageType.SYNC_UPDATE)
    return False


def _check_write(path: str, user: User) -> bool:
    try:
        require_can("write", path, user)
        return True
    except PermissionDenied:
        return False


class _PermissionCheckedChannel(Channel):
    """Wraps a ``Channel``, re-checking write permission on every message
    that can mutate the shared doc before it ever reaches ``room.serve()``.
    See module docstring for why this wraps the channel rather than using
    ``YRoom.on_message``.

    A rejected write-carrying frame closes the connection with code 1008 —
    the same close code (and the same frontend handling already built for
    the connect-time denial) a permission revocation should produce, so the
    client's existing reconnect/error UI is reused rather than needing a new
    signal. Raising ``StopAsyncIteration`` from ``recv()`` ends
    ``room.serve()``'s ``async for message in channel`` loop the same way a
    normal disconnect would (mirrors ``HttpxWebsocket.__anext__``'s own
    exception-to-``StopAsyncIteration`` conversion).
    """

    def __init__(self, inner: Channel, *, path: str, user: User, websocket: WebSocket) -> None:
        self._inner = inner
        self._path = path
        self._user = user
        self._websocket = websocket

    @property
    def path(self) -> str:
        return self._inner.path

    async def send(self, message: bytes) -> None:
        await self._inner.send(message)

    async def recv(self) -> bytes:
        message = await self._inner.recv()
        if _carries_write(message):
            allowed = await asyncio.to_thread(_check_write, self._path, self._user)
            if not allowed:
                await self._websocket.close(code=1008, reason="write permission required")
                raise StopAsyncIteration()
        return message


def _connect_sync(path: str, user: User) -> coedit.SessionRow:
    """The whole pre-accept handshake's DB/git work, bundled into one
    thread hop. ``require_can`` must run (and be free to raise
    ``PermissionDenied``) before ``websocket.accept()``."""
    require_can("read", path, user)
    head = wiki_git.head_sha_for_path(path)
    sess = coedit.open_session(path, base_sha=head)
    coedit.join(sess.id, user.id)
    return sess


@router.websocket("/ws/{path:path}")
async def ws(websocket: WebSocket, path: str, user: User = Depends(require_user_ws)) -> None:
    """``path`` is a URL path segment (``:path`` so a wiki path containing
    ``/`` survives as one segment), not a query param — matching
    ``y-websocket``'s client provider, which builds its connection URL as
    ``serverUrl/roomname`` with no query-param option (verified directly
    against its source).
    """
    try:
        sess = await asyncio.to_thread(_connect_sync, path, user)
    except PermissionDenied:
        await websocket.close(code=1008, reason="read permission required")
        return

    # Capture the room/tracker now, before serve() runs — WebsocketServer's
    # auto_clean_rooms pops the room out of SERVER.rooms itself the moment
    # the last client disconnects, *inside* the serve() call below, which
    # would otherwise race our own last-leave checkpoint's lookup below.
    room = await coedit_ws.get_or_create_room(path, session_id=sess.id)
    tracker = coedit_ws.tracker_for(path)

    await websocket.accept()
    channel = _PermissionCheckedChannel(
        HttpxWebsocket(websocket, path), path=path, user=user, websocket=websocket
    )
    coedit_ws.connect_user(path, user.id)
    try:
        await room.serve(channel)
    except WebSocketDisconnect:
        pass
    finally:
        coedit_ws.disconnect_user(path, user.id)
        # The leave must survive this task being cancelled (server shutdown;
        # the test portal tearing down the connection) — same cancellation-
        # safety pattern as app/tasks/coedit_leave.py's module docstring:
        # an awaited asyncio.to_thread executor item can be discarded before
        # any pool worker picks it up on cancellation, silently dropping the
        # leave. The fallback hands it to the coedit queue instead: a
        # synchronous enqueue nothing can cancel.
        try:
            await asyncio.to_thread(record_leave, sess.id, user.id, path)
        except asyncio.CancelledError:
            try:
                leave_coedit_session(sess.id, user.id, path)  # enqueue, no await
            except Exception:
                log.exception(
                    "coedit: queued-leave fallback failed for session %s", sess.id
                )
            raise
        if not await asyncio.to_thread(coedit.list_participants, sess.id):
            # Last participant left this process's connection to the room —
            # checkpoint now rather than waiting for the next scan tick.
            #
            # Fired as an independent task, not awaited inline: once the
            # client disconnects, the ASGI layer tears down *this*
            # coroutine's task promptly — an inline
            # `await checkpoint_ydoc_session(...)` here would get cut off
            # mid-checkpoint (the same reasoning the old branch's
            # `api/coedit_ws.py` documented and this route inherits).
            coedit_ws.drop_room(path)
            if tracker is not None:
                asyncio.create_task(_finish_checkpoint(sess.id, room, tracker, user.id))
            else:
                await asyncio.to_thread(coedit.close_if_clean, sess.id)
        log.info("coedit ws closed session=%s user=%s", sess.id, user.id)


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
        log.exception("coedit: checkpoint failed on last-leave for session %s", session_id)
    await asyncio.to_thread(coedit.close_if_clean, session_id)

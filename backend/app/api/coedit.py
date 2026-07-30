"""The page live-session channel — one WebSocket per session (cookie-authed
humans), driving the same domain logic (``app/wiki/coedit.py``) and
broadcast layer (``app/wiki/coedit_channel.py``) every message type in this
module shares. See ``plans/coedit-websocket-transport.md`` (if present) or
the originating conversation for the design rationale.

The first ``asyncio`` in this backend, scoped deliberately: ``async`` here
covers connection lifecycle only (accept, the recv/send loops, task
orchestration) — every ``_connect_sync``/``_handle_*``
helper below is a plain sync function, run via ``asyncio.to_thread`` from
the loops, with no idea it's being called from an async context at all. See
CLAUDE.md's "WebSocket routes" rule before adding another route like this
one.

A "co-edit session" is the page's *live session*: everyone viewing the page
joins it (read-gated — presence + real-time updates), and editing is a
capability inside it (``op``/``cursor``/``checkpoint`` messages are
write-gated, re-checked on *every* message, not just at connect, so a
mid-session ACL change takes effect immediately). Presence labels editors
vs viewers client-side from the live caret frames — a rendered caret IS the
"editing" state; the server stores nothing for it.

Participants leave after their shared heartbeat expires. A disconnect never
deletes shared presence based on one process's local socket registry — it
only commits the buffer, which needs no liveness information.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.auth import PermissionDenied, User, require_can
from app.auth.deps import require_user_ws
from app.models.coedit import (
    CheckpointMessage,
    CheckpointResultFrame,
    CursorMessage,
    GetOpsMessage,
    JoinedFrame,
    OpMessage,
    Operation,
    OpResultFrame,
    OpsResultFrame,
    ParticipantOut,
)
from app.tasks.coedit_checkpoint import checkpoint_coedit_session
from app.wiki import coedit, coedit_channel, git

router = APIRouter()
log = logging.getLogger(__name__)

# Idle silence before the send loop touches presence liveness + pings the
# client, so proxies don't consider the connection idle.
_HEARTBEAT_SECONDS = 15.0


def _participants_out(session_id: int) -> list[ParticipantOut]:
    return [
        ParticipantOut(
            user_id=p.user_id,
            user_display=p.user_display,
            joined_at=p.joined_at,
            last_seen_at=p.last_seen_at,
            last_edited_at=p.last_edited_at,
        )
        for p in coedit.list_participants(session_id)
    ]


class _WsActionError(Exception):
    """Internal — a write/read re-check failed for one inbound message.
    Caught per-handler and turned into a correlated error reply so the
    *connection* stays open — a rejected message is a per-message failure,
    not a reason to tear down the whole session."""

    def __init__(self, error: str) -> None:
        self.error = error


def _require_active(session_id: int, user: User, action: str) -> coedit.SessionRow:
    sess = coedit.get_session(session_id)
    if sess is None or sess.status != coedit.SessionStatus.ACTIVE.value:
        raise _WsActionError("no_active_session")
    try:
        require_can(action, sess.path, user)
    except PermissionDenied:
        raise _WsActionError("forbidden") from None
    return sess


def _handle_op(conn: coedit_channel.Connection, session_id: int, user: User, raw: dict[str, Any]) -> None:
    """All handlers below enqueue via ``conn.post`` (the connection's own
    ``coedit_channel`` queue) rather than calling ``websocket.send_json``
    directly. Found the hard way (a real test failure, not a hypothetical):
    ``_send_loop`` is *already* draining this queue and writing to the same
    socket concurrently with the recv loop — two tasks both calling
    ``send_json`` on one ``WebSocket`` races (and can interleave/corrupt)
    the frame writes, and there's no ordering guarantee between a direct
    reply and a same-op broadcast echo landing first. Routing everything
    through the one queue makes ``_send_loop`` the sole writer, and
    posting the reply *before* triggering the broadcast (see the ``op``
    case) makes the ordering deterministic: a sender always sees their own
    ``op_result`` before its broadcast echo, not racing it.
    """
    msg = OpMessage.model_validate(raw)
    try:
        _require_active(session_id, user, "write")
        out = coedit.apply_op(
            session_id,
            base_version=msg.base_version,
            changes=msg.changes,
            author_user_id=user.id,
            client_id=msg.client_id,
        )
    except _WsActionError as e:
        conn.post(OpResultFrame(request_id=msg.request_id, ok=False, error=e.error).model_dump(by_alias=True))
        return
    except ValueError:
        conn.post(
            OpResultFrame(request_id=msg.request_id, ok=False, error="invalid_op").model_dump(by_alias=True)
        )
        return
    if out is None:
        conn.post(
            OpResultFrame(request_id=msg.request_id, ok=False, error="stale_version").model_dump(by_alias=True)
        )
        return
    coedit.touch(session_id, user.id, edited=True)
    conn.post(
        OpResultFrame(request_id=msg.request_id, ok=True, version=out.version).model_dump(by_alias=True)
    )
    # caret_seq rides the frame so peers render the author's caret at the
    # edit; None (cleared caret / legacy client) = no caret assertion.
    coedit_channel.broadcast_op(
        session_id,
        out.version,
        msg.changes,
        user.id,
        client_id=msg.client_id,
        caret_seq=msg.caret_seq,
    )


def _handle_cursor(session_id: int, user: User, raw: dict[str, Any]) -> None:
    """Fire-and-forget: a rejected/failed cursor ping is silently dropped,
    never surfaced to the client, since the next throttled ping self-heals
    it (matches ``svc.ts``'s ``sendCursor``, which doesn't await a reply)."""
    msg = CursorMessage.model_validate(raw)
    try:
        _require_active(session_id, user, "write")
    except _WsActionError:
        return
    cleared = msg.anchor is None or msg.head is None
    coedit_channel.broadcast_cursor(
        session_id,
        user_id=user.id,
        # Match list_participants' SQL COALESCE(name, email) exactly — substitute
        # only on NULL, not on "" — so a peer's caret label and roster name agree.
        user_display=user.name if user.name is not None else user.email,
        anchor=None if cleared else msg.anchor,
        head=None if cleared else msg.head,
        typing=msg.typing and not cleared,
        seq=msg.seq,
    )


def _handle_checkpoint(conn: coedit_channel.Connection, session_id: int, user: User, raw: dict[str, Any]) -> None:
    msg = CheckpointMessage.model_validate(raw)
    try:
        _require_active(session_id, user, "write")
    except _WsActionError as e:
        conn.post(
            CheckpointResultFrame(
                request_id=msg.request_id, ok=False, error=e.error
            ).model_dump(by_alias=True)
        )
        return
    checkpoint_coedit_session(session_id)
    conn.post(CheckpointResultFrame(request_id=msg.request_id, ok=True).model_dump(by_alias=True))


def _handle_get_ops(conn: coedit_channel.Connection, session_id: int, user: User, raw: dict[str, Any]) -> None:
    msg = GetOpsMessage.model_validate(raw)
    try:
        sess = _require_active(session_id, user, "read")
    except _WsActionError as e:
        conn.post(
            OpsResultFrame(
                request_id=msg.request_id, ok=False, error=e.error, current_head_version=0, ops=[]
            ).model_dump(by_alias=True)
        )
        return
    # Head version + ops read as one consistent snapshot (see
    # ops_since_with_head), so a concurrent op can't desync them.
    result = coedit.ops_since_with_head(session_id, msg.since_version)
    conn.post(
        OpsResultFrame(
            request_id=msg.request_id,
            ok=True,
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
        ).model_dump(by_alias=True)
    )


async def _recv_loop(websocket: WebSocket, conn: coedit_channel.Connection, session_id: int, user: User) -> None:
    while True:
        raw = await websocket.receive_json()
        msg_type = raw.get("type")
        try:
            # Every handler below makes blocking DB calls (require_can,
            # coedit.apply_op, ...) — offloaded to a thread so one session's
            # DB round-trip doesn't stall every other WS connection sharing
            # this event loop. FastAPI only threadpools a plain `def` route
            # automatically; a sync call made *from inside* an `async def`
            # route (this one, since it's a WebSocket route) does not.
            if msg_type == "op":
                await asyncio.to_thread(_handle_op, conn, session_id, user, raw)
            elif msg_type == "cursor":
                await asyncio.to_thread(_handle_cursor, session_id, user, raw)
            elif msg_type == "checkpoint":
                await asyncio.to_thread(_handle_checkpoint, conn, session_id, user, raw)
            elif msg_type == "get_ops":
                await asyncio.to_thread(_handle_get_ops, conn, session_id, user, raw)
            else:
                log.warning("coedit ws: unknown message type %r", msg_type)
        except ValidationError:
            log.warning("coedit ws: malformed %r message", msg_type)


# The send loop waits on this event rather than parking a thread in a blocking
# queue read. A frame's publisher calls ``Connection.notify`` (see
# ``coedit_channel``) from whatever thread it is on, which schedules the set
# onto this loop — so no thread is held on a connection's behalf and there is
# no per-connection ceiling on how many sockets a process can serve. It also
# makes teardown ordinary asyncio: cancelling the send task interrupts an
# ``Event.wait`` immediately, where a thread parked in ``queue.Queue.get`` had
# no cancellation hook and needed a sentinel pushed into its own queue to come
# back.
def _make_notifier(loop: asyncio.AbstractEventLoop, ready: asyncio.Event) -> Callable[[], None]:
    def notify() -> None:
        try:
            loop.call_soon_threadsafe(ready.set)
        except RuntimeError:
            # The loop is closing (process shutting down). The connection is
            # going away with it, so an undelivered frame is moot.
            pass

    return notify


async def _send_loop(
    websocket: WebSocket, conn: coedit_channel.Connection, ready: asyncio.Event, session_id: int, user_id: str
) -> None:
    last_heartbeat = time.monotonic()
    while True:
        remaining = _HEARTBEAT_SECONDS - (time.monotonic() - last_heartbeat)
        if remaining > 0:
            try:
                await asyncio.wait_for(ready.wait(), remaining)
            except asyncio.TimeoutError:
                pass
        # Cleared *before* draining, so a frame published mid-drain re-sets it
        # and the next pass picks that frame up. Clearing after could drop the
        # wakeup for a frame already sitting in the queue.
        ready.clear()
        if time.monotonic() - last_heartbeat >= _HEARTBEAT_SECONDS:
            last_heartbeat = time.monotonic()
            alive = await asyncio.to_thread(coedit.touch, session_id, user_id)
            if not alive:
                return
            await websocket.send_json({"type": "ping"})
        while (frame := coedit_channel.drain(conn.queue, 0)) is not None:
            await websocket.send_json(frame)


def _connect_sync(path: str, user: User) -> coedit.SessionRow:
    """The whole pre-accept handshake's DB/git work, bundled into one
    thread hop. ``require_can`` must run (and be free to raise
    ``PermissionDenied``) before ``websocket.accept()`` — verified directly
    that an exception raised here still reaches the app's registered
    exception handler and produces a clean denial response pre-upgrade, the
    same as it does from a plain ``async def`` route body; offloading to a
    thread via ``asyncio.to_thread`` doesn't change that propagation."""
    require_can("read", path, user)
    while True:
        head = git.head_sha_for_path(path)
        initial = git.read_file_opt(path) or ""
        sess = coedit.open_session(path, base_sha=head, initial_buffer=initial)
        if coedit.join(sess.id, user.id):
            break
    # Announce the new participant to existing connections *before*
    # registering this one — otherwise the broadcast lands in our own queue
    # and the send loop would emit it on top of the inline `joined` frame
    # sent right after (a duplicate).
    coedit_channel.broadcast_presence(sess.id)
    return sess


@router.websocket("/ws")
async def ws(websocket: WebSocket, path: str, user: User = Depends(require_user_ws)) -> None:
    """``path`` is a query param (``?path=...``), not a URL segment — this
    app owns both ends of the connection URL, so there's no need for
    ``y-websocket``'s ``serverUrl/roomname`` convention.

    Joining is opening the page — presence + the live op stream — so
    connecting requires only read; *writing* (``op``/``cursor``/
    ``checkpoint``) is gated per-message inside the recv loop. A fresh
    session is seeded from the page's current HEAD; an already-open session
    is adopted as-is (its live buffer wins) — see ``coedit.open_session``.
    """
    sess = await asyncio.to_thread(_connect_sync, path, user)

    conn: coedit_channel.Connection | None = None
    try:
        await websocket.accept()
        ready = asyncio.Event()
        conn = coedit_channel.connect(
            sess.id, _make_notifier(asyncio.get_running_loop(), ready)
        )
        participants = await asyncio.to_thread(_participants_out, sess.id)
        await websocket.send_json(
            JoinedFrame(
                session_id=sess.id,
                buffer=sess.buffer_text,
                version=sess.version,
                base_sha=sess.base_sha,
                participants=participants,
            ).model_dump(by_alias=True)
        )

        recv_task = asyncio.create_task(_recv_loop(websocket, conn, sess.id, user))
        send_task = asyncio.create_task(_send_loop(websocket, conn, ready, sess.id, user.id))
        done, pending = await asyncio.wait(
            {recv_task, send_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    finally:
        if conn is not None:
            coedit_channel.disconnect(conn.id)
        # Commit this connection's tail now instead of waiting for the presence
        # scan to expire the heartbeat. Needs no liveness information: the
        # checkpoint no-ops on a clean session, and ``close_if_clean``'s
        # participant predicate keeps a session with live connections open, so
        # this is safe to run on every disconnect.
        #
        # The enqueue is a blocking Redis write and this body runs on the event
        # loop every other socket on this worker shares, so it's offloaded. It
        # must also survive this task being cancelled (server shutdown), and a
        # cancelled ``await`` isn't enough — the executor item it submitted can
        # be discarded before any pool worker picks it up — so that path
        # enqueues inline, where blocking the loop is moot anyway. Best-effort
        # either way; the periodic scan is the backstop.
        try:
            await asyncio.to_thread(checkpoint_coedit_session, sess.id)
        except asyncio.CancelledError:
            try:
                checkpoint_coedit_session(sess.id)
            except Exception:
                log.exception(
                    "coedit: queued-checkpoint fallback failed for session %s", sess.id
                )
            raise
        except Exception:
            log.exception(
                "coedit: checkpoint enqueue failed on disconnect for session %s", sess.id
            )
        log.info("coedit ws closed session=%s user=%s", sess.id, user.id)

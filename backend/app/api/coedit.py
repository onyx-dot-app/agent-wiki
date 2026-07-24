"""The page live-session channel — one WebSocket per session (cookie-authed
humans), driving the same domain logic (``app/wiki/coedit.py``) and
broadcast layer (``app/wiki/coedit_channel.py``) every message type in this
module shares. See ``plans/coedit-websocket-transport.md`` (if present) or
the originating conversation for the design rationale.

The first ``asyncio`` in this backend, scoped deliberately: ``async`` here
covers connection lifecycle only (accept, the recv/send loops, task
orchestration) — every ``_connect_sync``/``_disconnect_sync``/``_handle_*``
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

No client-sent "leave" message: the server's disconnect handler (``finally``
below) is the sole leave signal, firing on any connection loss — explicit
close, network drop, or a killed tab's socket dying — which doesn't depend
on the client successfully transmitting anything during teardown.
"""

from __future__ import annotations

import asyncio
import logging
import queue
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


def _handle_op(outbox: queue.Queue[coedit_channel.QueueItem], session_id: int, user: User, raw: dict[str, Any]) -> None:
    """All handlers below enqueue onto ``outbox`` (the connection's own
    ``coedit_channel`` queue) rather than calling ``websocket.send_json``
    directly. Found the hard way (a real test failure, not a hypothetical):
    ``_send_loop`` is *already* draining this queue and writing to the same
    socket concurrently with the recv loop — two tasks both calling
    ``send_json`` on one ``WebSocket`` races (and can interleave/corrupt)
    the frame writes, and there's no ordering guarantee between a direct
    reply and a same-op broadcast echo landing first. Routing everything
    through the one queue makes ``_send_loop`` the sole writer, and
    enqueueing the reply *before* triggering the broadcast (see the ``op``
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
        outbox.put_nowait(OpResultFrame(request_id=msg.request_id, ok=False, error=e.error).model_dump(by_alias=True))
        return
    except ValueError:
        outbox.put_nowait(
            OpResultFrame(request_id=msg.request_id, ok=False, error="invalid_op").model_dump(by_alias=True)
        )
        return
    if out is None:
        outbox.put_nowait(
            OpResultFrame(request_id=msg.request_id, ok=False, error="stale_version").model_dump(by_alias=True)
        )
        return
    coedit.touch(session_id, user.id, edited=True)
    outbox.put_nowait(
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


def _handle_checkpoint(outbox: queue.Queue[coedit_channel.QueueItem], session_id: int, user: User, raw: dict[str, Any]) -> None:
    msg = CheckpointMessage.model_validate(raw)
    try:
        _require_active(session_id, user, "write")
    except _WsActionError:
        outbox.put_nowait(CheckpointResultFrame(request_id=msg.request_id, ok=False).model_dump(by_alias=True))
        return
    checkpoint_coedit_session(session_id)
    outbox.put_nowait(CheckpointResultFrame(request_id=msg.request_id, ok=True).model_dump(by_alias=True))


def _handle_get_ops(outbox: queue.Queue[coedit_channel.QueueItem], session_id: int, user: User, raw: dict[str, Any]) -> None:
    msg = GetOpsMessage.model_validate(raw)
    try:
        sess = _require_active(session_id, user, "read")
    except _WsActionError as e:
        outbox.put_nowait(
            OpsResultFrame(
                request_id=msg.request_id, ok=False, error=e.error, current_head_version=0, ops=[]
            ).model_dump(by_alias=True)
        )
        return
    # Head version + ops read as one consistent snapshot (see
    # ops_since_with_head), so a concurrent op can't desync them.
    result = coedit.ops_since_with_head(session_id, msg.since_version)
    outbox.put_nowait(
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


async def _recv_loop(websocket: WebSocket, outbox: queue.Queue[coedit_channel.QueueItem], session_id: int, user: User) -> None:
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
                await asyncio.to_thread(_handle_op, outbox, session_id, user, raw)
            elif msg_type == "cursor":
                await asyncio.to_thread(_handle_cursor, session_id, user, raw)
            elif msg_type == "checkpoint":
                await asyncio.to_thread(_handle_checkpoint, outbox, session_id, user, raw)
            elif msg_type == "get_ops":
                await asyncio.to_thread(_handle_get_ops, outbox, session_id, user, raw)
            else:
                log.warning("coedit ws: unknown message type %r", msg_type)
        except ValidationError:
            log.warning("coedit ws: malformed %r message", msg_type)


# How often _send_loop's blocking drain call returns to check for
# cancellation. Short on purpose: asyncio.to_thread cancellation only
# unblocks the *awaiting* coroutine, not the underlying OS thread still
# parked in queue.Queue.get(timeout=...) — found the hard way, in
# production, not just in theory. Every disconnect (a WS closes, we
# reconnect) cancels this task; with a long timeout, the orphaned thread
# lingers for up to that long, still holding a slot in the shared default
# thread pool every asyncio.to_thread call in the process draws from. Under
# frequent reconnects, orphaned threads accumulate faster than they drain,
# eventually exhausting the pool — at which point *every* asyncio.to_thread
# call across the whole backend (new connects, every op/cursor/checkpoint
# message, for every session) queues for a free worker. That reads as "the
# whole app froze," and can plausibly cause more disconnects itself (a
# starved event loop can miss uvicorn's own ping/pong window and get
# closed as unresponsive) — a self-reinforcing spiral. Bounding the poll to
# ~1s bounds any orphaned thread's worst case to ~1s instead of 15.
_SEND_LOOP_POLL_SECONDS = 1.0


async def _send_loop(
    websocket: WebSocket, conn: coedit_channel.Connection, session_id: int, user_id: str
) -> None:
    idle_elapsed = 0.0
    while True:
        frame = await asyncio.to_thread(coedit_channel.drain, conn.queue, _SEND_LOOP_POLL_SECONDS)
        if frame is coedit_channel.CLOSE_SIGNAL:
            # ws()'s teardown path pushed this via coedit_channel.wake() right
            # before cancelling this task — reachable here (not just via
            # cancellation) because there's a real race between the two: this
            # drain call may already have picked up the signal as an ordinary
            # queue item before the asyncio-level cancellation is delivered.
            # Either path ends the loop; this one just does it without
            # waiting on a CancelledError that might arrive after the value
            # already did.
            return
        if frame is None:
            idle_elapsed += _SEND_LOOP_POLL_SECONDS
            if idle_elapsed >= _HEARTBEAT_SECONDS:
                idle_elapsed = 0.0
                await asyncio.to_thread(coedit.touch, session_id, user_id)
                await websocket.send_json({"type": "ping"})
            continue
        idle_elapsed = 0.0
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
    head = git.head_sha_for_path(path)
    initial = git.read_file_opt(path) or ""
    sess = coedit.open_session(path, base_sha=head, initial_buffer=initial)
    coedit.join(sess.id, user.id)
    # Announce the new participant to existing connections *before*
    # registering this one — otherwise the broadcast lands in our own queue
    # and the send loop would emit it on top of the inline `joined` frame
    # sent right after (a duplicate).
    coedit_channel.broadcast_presence(sess.id)
    return sess


def _disconnect_sync(session_id: int, user_id: str) -> None:
    # Only mark the user gone when their last connection closes, so a
    # second tab doesn't evict them.
    still = coedit_channel.user_still_connected(session_id, user_id)
    log.info("coedit leave session=%s user=%s still_connected=%s", session_id, user_id, still)
    if not still:
        coedit.leave(session_id, user_id)
        coedit_channel.broadcast_presence(session_id)
        _checkpoint_if_last_left(session_id)
        log.info("coedit leave done session=%s", session_id)


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

    # `_connect_sync` already registered the participant (`coedit.join`)
    # before returning, so from here on a disconnect — including one during
    # `accept()` itself, which the client can trigger mid-handshake since
    # `_connect_sync`'s git/DB work takes measurable time — must still reach
    # `_disconnect_sync` to undo it. Otherwise the user is stuck as a
    # phantom participant forever, which also blocks
    # `_checkpoint_if_last_left` from ever firing for this session.
    conn: coedit_channel.Connection | None = None
    try:
        await websocket.accept()
        conn = coedit_channel.connect(sess.id, user.id)
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

        recv_task = asyncio.create_task(_recv_loop(websocket, conn.queue, sess.id, user))
        send_task = asyncio.create_task(_send_loop(websocket, conn, sess.id, user.id))
        done, pending = await asyncio.wait(
            {recv_task, send_task}, return_when=asyncio.FIRST_COMPLETED
        )
        log.info(
            "coedit ws wait returned session=%s done=%s",
            sess.id,
            [("recv" if t is recv_task else "send") for t in done],
        )
        # Wake a still-blocked drain() *before* cancelling it: cancelling
        # send_task only stops the asyncio Task watching the thread, not the
        # already-dispatched OS thread itself — queue.Queue.get(timeout=...)
        # has no cancellation hook, so without this it just keeps blocking,
        # doing nothing, until its own timeout expires (see
        # coedit_channel.wake's docstring). Pushing CLOSE_SIGNAL is what
        # actually unblocks that thread immediately, same as a real frame
        # arriving would.
        coedit_channel.wake(conn.id)
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
        task = asyncio.current_task()
        log.info(
            "coedit ws finally session=%s cancelling=%s",
            sess.id,
            task.cancelling() if task is not None else "?",
        )
        await asyncio.to_thread(_disconnect_sync, sess.id, user.id)
        log.info("coedit ws closed session=%s user=%s", sess.id, user.id)

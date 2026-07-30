"""The page live-session channel — one WebSocket per session (cookie-authed
humans), speaking raw Yjs sync/awareness protocol bytes over binary frames,
plus a small set of JSON control messages over text frames. Driven by
session/participant bookkeeping in ``app/wiki/coedit.py``, the document rebuilt
on demand in ``app/wiki/coedit_live.py``, and the broadcast layer in
``app/wiki/coedit_channel.py``.

``async`` here covers connection lifecycle (accept, the recv/send loops,
task orchestration) plus — deliberately, unlike every other WebSocket route
in this backend — the Yjs sync/awareness message handling itself. See
CLAUDE.md's "WebSocket routes" rule before adding another route like this
one; this one departs from it on purpose for pycrdt calls specifically:
``Doc``/``Awareness`` are PyO3 "unsendable" Rust types (thread-affine), so
unlike a normal blocking call, they must run inline on this task's own
thread (the event loop), never via ``asyncio.to_thread``'s shared worker
pool. It's also a good fit regardless:
in-memory CRDT math is fast, not the kind of blocking call that rule exists
to keep off the loop. Every DB/git call (participant tracking, permission
checks, the checkpoint's git commit) still goes through
``asyncio.to_thread`` as usual.

A "co-edit session" is the page's *live session*: everyone viewing the page
joins it (read-gated — presence + the live document), and writing (applying
sync/awareness updates that change content, checkpointing) is a capability
inside it, gated once at connect time via ``can_write`` and closed over for
the connection's lifetime — a known, carried-forward limitation (a
mid-session ACL change doesn't take effect until reconnect), not solved
here. Presence labels editors vs viewers client-side from Awareness state —
a rendered caret IS the "editing" state; the server stores nothing for it
beyond relaying it.

No client-sent "leave" message: the server's disconnect handler (``finally``
below) is the sole leave signal, firing on any connection loss — explicit
close, network drop, or a killed tab's socket dying — which doesn't depend
on the client successfully transmitting anything during teardown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pycrdt import (
    create_update_message,
    YMessageType,
    YSyncMessageType,
    read_message,
)
from pydantic import ValidationError

from app.auth import User, require_can
from app.auth.deps import require_user_ws
from app.models.coedit import (
    CheckpointMessage,
    CheckpointResultFrame,
    GetUpdatesSinceMessage,
    JoinedFrame,
    JoinErrorFrame,
    ParticipantOut,
)
from app.tasks.coedit_checkpoint import checkpoint_coedit_session_task
from app.wiki import acl, coedit, coedit_channel, coedit_live, git
from app.wiki.markdown_yjs import seed_doc_from_markdown

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


def _connect_sync(path: str, user: User) -> tuple[coedit.SessionRow, bool]:
    """The DB/permission/git work of the pre-accept handshake — blocking,
    but no ``Doc`` access, so safe on any thread. ``require_can`` must run
    (and be free to raise ``PermissionDenied``) before ``websocket.accept()``
    — verified directly that an exception raised here still reaches the
    app's registered exception handler and produces a clean denial response
    pre-upgrade, the same as it does from a plain ``async def`` route body;
    offloading to a thread via ``asyncio.to_thread`` doesn't change that
    propagation.

    Seeding the session's first snapshot, if it still needs one, is a separate
    step — see ``_ensure_snapshot``.
    """
    require_can("read", path, user)
    can_write = "write" in acl.effective(user.id, user.is_admin, path)
    head = git.head_sha_for_path(path)
    sess = coedit.open_session(path, base_sha=head)
    coedit.join(sess.id, user.id)
    # Announce the new participant to existing connections *before*
    # registering this one — otherwise the broadcast lands in our own queue
    # and the send loop would emit it on top of the inline `joined` frame
    # sent right after (a duplicate).
    coedit_channel.broadcast_presence(sess.id)
    return sess, can_write


def _sync_update_frame(update: bytes) -> bytes:
    """Wrap raw update bytes as a y-protocol SYNC_UPDATE message.

    The log stores the bare update (what ``read_message`` yielded on the way
    in), so replaying one to a client means re-adding the framing its Yjs
    provider expects.
    """
    return create_update_message(update)


def _apply_yjs_frame(
    conn: coedit_channel.Connection,
    session_id: int,
    can_write: bool,
    raw: bytes,
) -> bytes | None:
    """Validate, then hand back the update bytes to durably log; ``None`` when
    nothing content-changing happened.

    There is no resident ``Doc`` to mutate here. A client update is checked for
    integrability against a scratch doc (~2 µs) and then appended to the log,
    which *is* the document — so the server never holds a replica that could
    drift from the protocol. A ``SYNC_STEP1`` is answered by building a doc for
    that one call (``coedit_live.sync_reply``).

    Ordering needs nothing extra: ``coedit.apply_update`` assigns ``ydoc_seq``
    in a single atomic ``UPDATE … RETURNING``, and CRDT updates commute anyway,
    so the log's order is a delivery aid rather than a correctness requirement.
    """
    if not raw:
        return None
    msg_type = raw[0]
    if msg_type == YMessageType.SYNC:
        inner = raw[1:]
        if not inner:
            return None
        sync_type = inner[0]
        is_content = sync_type in (YSyncMessageType.SYNC_STEP2, YSyncMessageType.SYNC_UPDATE)
        if is_content and not can_write:
            return None
        if not is_content:
            # SYNC_STEP1: reply with whatever this client is missing, computed
            # from a doc built for this call and dropped with it.
            try:
                reply = coedit_live.sync_reply(session_id, raw)
            except coedit_live.SessionGone:
                return None
            if reply is not None:
                conn.post(coedit_channel.YjsBytes(payload=reply))
            return None
        update = read_message(inner[1:])
        if update == b"\x00\x00":
            return None  # empty update (pycrdt's own "nothing to apply" marker)
        if not coedit_live.validate_update(update):
            log.warning(
                "coedit: session %s rejected an unintegrable update (%d bytes)",
                session_id,
                len(update),
            )
            return None
        return update
    if msg_type == YMessageType.AWARENESS:
        if not can_write:
            return None  # a read-only viewer has no caret to show
        # Relayed as opaque bytes. The server holds no Awareness: nothing reads
        # its states, and a late joiner receives no awareness backlog either
        # way, so there is nothing for a resident copy to serve.
        coedit_channel.broadcast_yjs(session_id, raw)
        return None
    return None


async def _recv_loop(
    websocket: WebSocket,
    conn: coedit_channel.Connection,
    session_id: int,
    user: User,
    can_write: bool,
) -> None:
    while True:
        # Low-level receive() (not receive_bytes()/receive_json()) so one
        # loop can discriminate binary Yjs frames from text control frames —
        # it doesn't raise on disconnect the way the typed helpers do, so
        # that's checked and raised explicitly below.
        msg = await websocket.receive()
        if msg["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(msg["code"], msg.get("reason"))

        data = msg.get("bytes")
        if data is not None:
            update_bytes = await asyncio.to_thread(
                _apply_yjs_frame, conn, session_id, can_write, data
            )
            if update_bytes is not None:
                # Log *before* relaying, so the broadcast can carry the seq the
                # log assigned: that seq is what lets a peer notice a dropped
                # relay and fetch what it missed. Relaying first would leave the
                # gap undetectable.
                seq = await asyncio.to_thread(
                    coedit.apply_update,
                    session_id,
                    update_bytes=update_bytes,
                    author_user_id=user.id,
                )
                if seq is None:
                    continue  # session closed underneath us; nothing to relay
                coedit_channel.broadcast_yjs(session_id, data, seq)
                await asyncio.to_thread(coedit.touch, session_id, user.id, edited=True)
            continue

        text = msg.get("text")
        if text is None:
            continue
        try:
            parsed: Any = json.loads(text)
        except ValueError:
            log.warning("coedit ws: malformed text frame")
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "get_updates_since":
            try:
                since = GetUpdatesSinceMessage.model_validate(parsed)
            except ValidationError:
                continue
            # A client that saw a gap in the relay stream asks for what it
            # missed. Room-less this is the whole recovery path for a dropped
            # relay — without it a client stays diverged until it reconnects.
            missed = await asyncio.to_thread(coedit.updates_since, session_id, since.since_seq)
            for u in missed.updates:
                conn.post(
                    coedit_channel.YjsBytes(
                        payload=_sync_update_frame(u.update_payload), seq=u.seq
                    )
                )
            continue
        if not isinstance(parsed, dict) or parsed.get("type") != "checkpoint":
            log.warning("coedit ws: unknown text message %r", parsed)
            continue
        try:
            checkpoint_msg = CheckpointMessage.model_validate(parsed)
        except ValidationError:
            continue
        if not can_write:
            conn.post(
                CheckpointResultFrame(
                    request_id=checkpoint_msg.request_id, ok=False, error="forbidden"
                ).model_dump()
            )
            continue
        # Enqueue rather than await in-process: the checkpoint engine needs
        # nothing from this connection, so there's no reason to block this
        # recv loop on a commit-plus-merge.
        # The task itself publishes the CheckpointResultFrame ack once it
        # completes (broadcast to the session, this connection included —
        # see checkpoint_coedit_session_task).
        await asyncio.to_thread(
            checkpoint_coedit_session_task, session_id, request_id=checkpoint_msg.request_id
        )


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
# call across the whole backend (new connects, every message, every
# checkpoint) queues for a free worker. That reads as "the whole app
# froze," and can plausibly cause more disconnects itself (a starved event
# loop can miss uvicorn's own ping/pong window and get closed as
# unresponsive) — a self-reinforcing spiral. Bounding the poll to ~1s
# bounds any orphaned thread's worst case to ~1s instead of 15.
# The send loop waits on this event rather than parking a thread in a blocking
# queue read. A publisher calls ``Connection.notify`` (see ``coedit_channel``)
# from whatever thread it is on, which schedules the set onto this loop — so no
# thread is held on a connection's behalf and there is no per-connection ceiling
# on how many sockets a process can serve. It also makes teardown ordinary
# asyncio: cancelling the send task interrupts an ``Event.wait`` immediately.
def _make_notifier(loop: asyncio.AbstractEventLoop, ready: asyncio.Event) -> Callable[[], None]:
    def notify() -> None:
        try:
            loop.call_soon_threadsafe(ready.set)
        except RuntimeError:
            # The loop is closing (process shutting down); the connection is
            # going away with it, so an undelivered item is moot.
            pass

    return notify


async def _send_loop(
    websocket: WebSocket,
    conn: coedit_channel.Connection,
    ready: asyncio.Event,
    session_id: int,
    user_id: str,
) -> None:
    last_heartbeat = time.monotonic()
    while True:
        remaining = _HEARTBEAT_SECONDS - (time.monotonic() - last_heartbeat)
        if remaining > 0:
            try:
                await asyncio.wait_for(ready.wait(), remaining)
            except asyncio.TimeoutError:
                pass
        # Cleared *before* draining, so an item published mid-drain re-sets it
        # and the next pass picks it up.
        ready.clear()
        if time.monotonic() - last_heartbeat >= _HEARTBEAT_SECONDS:
            last_heartbeat = time.monotonic()
            alive = await asyncio.to_thread(coedit.touch, session_id, user_id)
            if not alive:
                # Our participant row expired, so this socket is no longer part
                # of the session — close and let the client reconnect.
                return
            await websocket.send_json({"type": "ping"})
        while (item := coedit_channel.drain(conn.queue, 0)) is not None:
            if isinstance(item, coedit_channel.YjsBytes):
                await websocket.send_bytes(item.payload)
            else:
                await websocket.send_json(item)

def _seed_snapshot_sync(session_id: int, path: str, base_sha: str | None) -> bool:
    """Give a brand-new session its initial snapshot; True if the page is
    representable (or already has one).

    Runs entirely in one thread hop, and that is deliberate: the ``Doc`` built
    here is a PyO3 unsendable type, so it is created, read (``get_update``) and
    dropped without ever leaving this call. Nothing keeps it — the snapshot
    bytes plus the update log are the document from here on. The
    already-seeded check belongs in here for the same reason: it keeps the
    common case (every connection after the first) to one hop and skips the
    git read.

    ``set_initial_snapshot`` is conditional on ``ydoc_snapshot IS NULL``, so
    two processes connecting at once race harmlessly: the loser's snapshot is
    discarded and both then rebuild from the winner's, which is the same
    lineage either way.
    """
    if coedit.has_snapshot(session_id):
        return True
    body = git.read_file_opt(path, base_sha or "HEAD") or ""
    try:
        doc = seed_doc_from_markdown(body)
    except NotImplementedError:
        return False
    coedit.set_initial_snapshot(session_id, doc.get_update(), body)
    return True


async def _ensure_snapshot(
    sess: coedit.SessionRow, user: User, websocket: WebSocket
) -> bool:
    """Make sure this session is rebuildable, i.e. has a snapshot to replay from.

    Only a session nobody has connected to yet lacks one. Any failure here has
    to undo the ``coedit.join`` that ``_connect_sync`` already did, or the
    caller lingers as a participant whose heartbeat nothing refreshes.
    """
    try:
        ok = await asyncio.to_thread(
            _seed_snapshot_sync, sess.id, sess.path, sess.base_sha
        )
    except (Exception, asyncio.CancelledError):
        await asyncio.to_thread(coedit.leave, sess.id, user.id)
        await asyncio.to_thread(coedit_channel.broadcast_presence, sess.id)
        raise
    if not ok:
        # The page uses markdown the codec can't represent as a Yjs doc. Close
        # the session rather than leave a half-usable one behind.
        log.warning(
            "coedit ws: session %s path %r uses unsupported formatting; refusing",
            sess.id,
            sess.path,
        )
        await asyncio.to_thread(coedit.leave, sess.id, user.id)
        await asyncio.to_thread(coedit_channel.broadcast_presence, sess.id)
        await asyncio.to_thread(coedit.close_session, sess.id)
        await websocket.accept()
        await websocket.send_json(
            JoinErrorFrame(
                detail="This page uses formatting the live editor doesn't support yet."
            ).model_dump()
        )
        await websocket.close()
        return False
    return True


@router.websocket("/ws")
async def ws(websocket: WebSocket, path: str, user: User = Depends(require_user_ws)) -> None:
    """``path`` is a query param (``?path=...``), not a URL segment — this
    app owns both ends of the connection URL, so there's no need for
    ``y-websocket``'s ``serverUrl/roomname`` convention.

    Joining is opening the page — presence + the live document — so
    connecting requires only read; see the module docstring for the write
    gate.

    Nothing process-local is set up here. The document is whatever
    ``(ydoc_snapshot, coedit_updates)`` replays to, so every connection —
    first or thousandth, this worker or another — answers from the same
    durable state. Only a session that has never been connected to anywhere
    gets seeded from the page's git HEAD (``_ensure_snapshot``); seeding on
    each connect would mint a fresh CRDT lineage per worker, and an update
    logged against one lineage integrates into neither the other's nor a
    later checkpoint's replay.
    """
    sess, can_write = await asyncio.to_thread(_connect_sync, path, user)
    if not await _ensure_snapshot(sess, user, websocket):
        return

    # `_connect_sync` already registered the participant (`coedit.join`).
    # Nothing here undoes that: presence is a lease on the participant row's
    # heartbeat, so a connection that dies — including one lost during
    # `accept()` itself — simply stops refreshing it and the periodic scan
    # expires it (`coedit.expire_stale_participants`). No process ever deletes
    # presence from its own view of its own sockets.
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
                base_sha=sess.base_sha,
                can_write=can_write,
                participants=participants,
            ).model_dump()
        )
        # Offer our state so the new client's Yjs provider can request
        # exactly what it's missing — the standard two-way sync handshake.
        # A personal query sent directly to this connection only, never
        # broadcast.
        await websocket.send_bytes(
            await asyncio.to_thread(coedit_live.initial_sync_message, sess.id)
        )

        recv_task = asyncio.create_task(
            _recv_loop(websocket, conn, sess.id, user, can_write)
        )
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
        # Commit this connection's tail now rather than waiting for the
        # presence scan to expire the heartbeat. Needs no liveness information:
        # the checkpoint no-ops on a clean session, and ``close_if_clean``'s
        # participant predicate keeps a session with live connections open.
        #
        # Offloaded because the enqueue is a blocking Redis write and this body
        # runs on the event loop every other socket shares. It must also survive
        # this task being cancelled (server shutdown), and a cancelled ``await``
        # is not enough — the executor item can be discarded before any pool
        # worker picks it up — so that path enqueues inline, where blocking the
        # loop is moot anyway.
        try:
            await asyncio.to_thread(checkpoint_coedit_session_task, sess.id)
        except asyncio.CancelledError:
            try:
                checkpoint_coedit_session_task(sess.id)
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

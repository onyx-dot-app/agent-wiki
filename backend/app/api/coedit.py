"""The page live-session channel — one WebSocket per session (cookie-authed
humans), speaking raw Yjs sync/awareness protocol bytes over binary frames,
plus a small set of JSON control messages over text frames. Driven by
session/participant bookkeeping in ``app/wiki/coedit.py``, the in-process
live document in ``app/wiki/coedit_room.py``, and the broadcast layer in
``app/wiki/coedit_channel.py``. See ``plans/valiant-tickling-reddy.md`` (if
still present) or the originating conversation for the design rationale.

``async`` here covers connection lifecycle (accept, the recv/send loops,
task orchestration) plus — deliberately, unlike every other WebSocket route
in this backend — the Yjs sync/awareness message handling itself. See
CLAUDE.md's "WebSocket routes" rule before adding another route like this
one; this one departs from it on purpose for pycrdt calls specifically:
``Doc``/``Awareness`` are PyO3 "unsendable" Rust types (thread-affine), so
unlike a normal blocking call, they must run inline on this task's own
thread (the event loop), never via ``asyncio.to_thread``'s shared worker
pool — see ``app/wiki/coedit_room.py``. It's also a good fit regardless:
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
import queue
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pycrdt import (
    Doc,
    YMessageType,
    YSyncMessageType,
    create_sync_message,
    handle_sync_message,
    read_message,
)
from pydantic import ValidationError

from app.auth import User, require_can
from app.auth.deps import require_user_ws
from app.models.coedit import (
    CheckpointMessage,
    CheckpointResultFrame,
    JoinedFrame,
    JoinErrorFrame,
    ParticipantOut,
)
from app.tasks.coedit_checkpoint import checkpoint_coedit_session_task
from app.tasks.coedit_leave import leave_coedit_session, record_leave
from app.wiki import acl, coedit, coedit_channel, coedit_room, git
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

    Room creation, if needed, happens separately back on the event loop —
    see ``ws()`` — since constructing a ``Doc`` must happen on the thread
    that will go on to use it.
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


def _read_snapshot_for_rehydrate(
    session_id: int,
) -> tuple[coedit.CheckpointSessionRow, coedit.UpdatesSince] | None:
    """Read ``(ydoc_snapshot, updates-logged-since-that-snapshot)`` as one
    consistent pair, under ``checkpoint_lock`` — without the lock, a
    checkpoint's own atomic prune+advance running *between* these two
    otherwise-separate reads can leave a gap: by the time the second read
    runs, the checkpoint may have already pruned rows this call needed to
    bridge the snapshot it already read to the session's actual current
    state, silently reconstructing a doc that's missing content (confirmed
    in review).

    Returns ``None`` if the session has no snapshot yet (a brand-new
    session — the caller falls back to seeding fresh from git).

    Raises if the lock can't be acquired within
    ``coedit.checkpoint_lock``'s own timeout (a checkpoint has held it for
    the full 30s — rare, a checkpoint is normally ms-to-low-seconds):
    deliberately not a silent fallback to a fresh git-seed here, since a
    snapshot *does* exist in that case and seeding fresh would put this
    room on an incompatible CRDT lineage from whatever the in-flight
    checkpoint is about to persist — exactly the bug this function exists
    to close. The client's own reconnect loop retries the whole join
    shortly after.
    """
    with coedit.checkpoint_lock(session_id) as acquired:
        if not acquired:
            raise RuntimeError(f"coedit ws: checkpoint lock busy for session {session_id}")
        sess_ck = coedit.get_session_for_checkpoint(session_id)
        if sess_ck is None or sess_ck.ydoc_snapshot is None:
            return None
        since = coedit.updates_since(session_id, sess_ck.ydoc_snapshot_seq)
        return sess_ck, since


def _apply_yjs_frame(
    outbox: queue.Queue[coedit_channel.QueueItem],
    session_id: int,
    room: coedit_room.Room,
    can_write: bool,
    raw: bytes,
) -> bytes | None:
    """``Doc``/``Awareness``-touching only — runs inline on the event loop
    (see module docstring), never offloaded via ``asyncio.to_thread``.

    Returns the raw update bytes to durably log via ``coedit.apply_update``,
    or ``None`` if nothing content-changing happened (a reply-only
    SYNC_STEP1, an awareness frame, an empty/no-op update, or a write
    attempt from a read-only viewer, silently dropped).
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
        # handle_sync_message expects the message *without* the leading
        # YMessageType.SYNC byte (still carrying its own YSyncMessageType
        # byte) — confirmed against the installed pycrdt, not assumed. Its
        # reply (SYNC_STEP2, only for a SYNC_STEP1 input) is *already* a
        # complete, ready-to-send message with its own YMessageType.SYNC
        # prefix (create_sync_step2_message adds it internally) — sent
        # as-is, never re-wrapped.
        reply = handle_sync_message(inner, room.doc)
        if reply is not None:
            outbox.put_nowait(coedit_channel.YjsBytes(payload=reply))
        if not is_content:
            return None
        update = read_message(inner[1:])
        if update == b"\x00\x00":
            return None  # empty update (pycrdt's own "nothing to apply" marker)
        # Bumped synchronously with the room.doc mutation handle_sync_message
        # just applied above — deliberately *not* tied to the DB log write
        # this update's caller awaits next (coedit.apply_update, a separate
        # asyncio.to_thread step): a reader that wants to know "has this
        # room's Doc changed since I looked" needs a signal that moves in
        # lockstep with the Doc itself, not with the DB's own watermark,
        # which can lag behind it by a real await gap (see Room.generation's
        # own docstring — this was a genuine data-loss bug in
        # coedit_rebase.py, caught in review).
        room.generation += 1
        coedit_channel.broadcast_yjs(session_id, raw)
        return update
    if msg_type == YMessageType.AWARENESS:
        if not can_write:
            return None  # a read-only viewer has no caret to show
        payload = read_message(raw[1:])
        room.awareness.apply_awareness_update(payload, "remote")
        coedit_channel.broadcast_yjs(session_id, raw)
        return None
    return None


async def _recv_loop(
    websocket: WebSocket,
    outbox: queue.Queue[coedit_channel.QueueItem],
    session_id: int,
    user: User,
    room: coedit_room.Room,
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
            update_bytes = _apply_yjs_frame(outbox, session_id, room, can_write, data)
            if update_bytes is not None:
                await asyncio.to_thread(
                    coedit.apply_update,
                    session_id,
                    update_bytes=update_bytes,
                    author_user_id=user.id,
                )
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
        if not isinstance(parsed, dict) or parsed.get("type") != "checkpoint":
            log.warning("coedit ws: unknown text message %r", parsed)
            continue
        try:
            checkpoint_msg = CheckpointMessage.model_validate(parsed)
        except ValidationError:
            continue
        if not can_write:
            outbox.put_nowait(
                CheckpointResultFrame(request_id=checkpoint_msg.request_id, ok=False).model_dump()
            )
            continue
        # Enqueue rather than await in-process: the checkpoint engine no
        # longer needs this (or any specific) process's live room, so
        # there's no reason to block this connection's recv loop on it.
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
_SEND_LOOP_POLL_SECONDS = 1.0


async def _send_loop(
    websocket: WebSocket, conn: coedit_channel.Connection, session_id: int, user_id: str
) -> None:
    idle_elapsed = 0.0
    while True:
        item = await asyncio.to_thread(coedit_channel.drain, conn.queue, _SEND_LOOP_POLL_SECONDS)
        if item is coedit_channel.CLOSE_SIGNAL:
            # ws()'s teardown path pushed this via coedit_channel.wake() right
            # before cancelling this task — reachable here (not just via
            # cancellation) because there's a real race between the two: this
            # drain call may already have picked up the signal as an ordinary
            # queue item before the asyncio-level cancellation is delivered.
            # Either path ends the loop; this one just does it without
            # waiting on a CancelledError that might arrive after the value
            # already did.
            return
        if item is None:
            idle_elapsed += _SEND_LOOP_POLL_SECONDS
            if idle_elapsed >= _HEARTBEAT_SECONDS:
                idle_elapsed = 0.0
                await asyncio.to_thread(coedit.touch, session_id, user_id)
                await websocket.send_json({"type": "ping"})
            continue
        idle_elapsed = 0.0
        if isinstance(item, coedit_channel.YjsBytes):
            await websocket.send_bytes(item.payload)
        else:
            await websocket.send_json(item)


async def _resolve_room(
    sess: coedit.SessionRow, user: User, websocket: WebSocket
) -> coedit_room.Room | None:
    """Build (or rehydrate) this session's room when this process doesn't
    already hold one yet — the ``room is None`` branch of ``ws()``, pulled
    out on its own so the whole thing can be wrapped in one cleanup: by
    the time this runs, ``_connect_sync`` has already called
    ``coedit.join`` and broadcast the new participant, so *any* exception
    escaping from here (not just the already-handled
    ``NotImplementedError`` case below) must undo both first, or the
    caller is stuck as a phantom participant forever — including blocking
    the session's last-leave checkpoint trigger from ever firing
    (confirmed in review: ``_read_snapshot_for_rehydrate`` raising on a
    busy ``checkpoint_lock`` did exactly this, since that raise landed
    before any cleanup existed for it).

    Returns ``None`` after fully handling a codec failure itself (sends a
    ``JoinErrorFrame``, closes the socket) — the caller must return
    immediately without trying to proceed as if a room exists.
    """
    try:
        rehydrate = await asyncio.to_thread(_read_snapshot_for_rehydrate, sess.id)
        if rehydrate is not None:
            sess_ck, since = rehydrate
            assert sess_ck.ydoc_snapshot is not None  # _read_snapshot_for_rehydrate guarantees this
            # Doc construction + apply_update — must run inline on this
            # task's own thread (the event loop), not via to_thread; see
            # coedit_room.py.
            doc = Doc()
            doc.apply_update(sess_ck.ydoc_snapshot)
            for u in since.updates:
                try:
                    doc.apply_update(u.update_payload)
                except Exception:
                    # An undecodable row can't be allowed to make the page
                    # permanently unopenable — skip it and log, same
                    # reasoning as coedit_checkpoint.py's _rebuild_doc.
                    log.exception(
                        "coedit ws: session %s seq %d update failed to apply during"
                        " rehydrate; skipping",
                        sess.id,
                        u.seq,
                    )
            base_body = sess_ck.ydoc_snapshot_body
            return coedit_room.create_room(sess.id, sess.path, doc, base_body, sess.base_sha)

        body = await asyncio.to_thread(git.read_file_opt, sess.path, sess.base_sha or "HEAD")
        base_body = body or ""
        try:
            doc = seed_doc_from_markdown(base_body)  # Doc construction — inline, see above
        except NotImplementedError:
            # A construct the codec can't encode (an image, a code block
            # inside a list item, etc. — see markdown_yjs.py's module
            # docstring) makes this page permanently unable to open in the
            # live editor *as this session*; retrying changes nothing,
            # since the input is deterministic.
            #
            # Also close the session itself (caught in review): left
            # ACTIVE, open_session's own "reuse any ACTIVE row for this
            # path" means every future join attempt reuses *this* pinned
            # base_sha/NULL-snapshot row forever, re-hitting the same git
            # blob even after the offending content is fixed at HEAD —
            # close_session is otherwise only reachable from
            # checkpoint_session's missing-path branch, never a clean
            # session like this one, so nothing else would ever do it. A
            # closed, ydoc_seq==0, no-participants row is inert — closing
            # it doesn't discard anything, since no room, no snapshot, and
            # no updates were ever created for it.
            #
            # Accept then close, rather than denying pre-upgrade (raising
            # here, as a PermissionDenied-style exception would): a real
            # browser's WebSocket API can't see a pre-accept HTTP
            # rejection's status/body at all (only the test client's
            # WebSocketDenialResponse can), so the only way to hand the
            # frontend a distinguishable, non-retryable reason is a real
            # frame sent after a real accept().
            log.warning(
                "coedit ws: %s has a construct the codec can't encode; denying join",
                sess.path,
                exc_info=True,
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
            return None
        room = coedit_room.create_room(sess.id, sess.path, doc, base_body, sess.base_sha)
        # A Doc read (get_update) — must run inline on this task's own
        # thread (the event loop, which just constructed the Doc), not
        # via to_thread; see coedit_room.py. The DB write itself is plain
        # bytes, offloaded normally. Conditional on ydoc_snapshot IS NULL,
        # so this is safe to call every time a process creates a room for
        # a session it didn't already know about — only the very first
        # room, ever, for a brand-new session actually persists here.
        snapshot = room.doc.get_update()
        won = await asyncio.to_thread(coedit.set_initial_snapshot, sess.id, snapshot, base_body)
        if not won:
            # Lost the race: two processes both saw ydoc_snapshot IS NULL
            # and seeded independently (each seed_doc_from_markdown call
            # invents its own CRDT lineage — see markdown_yjs.py), and the
            # other process's write landed first. This process's freshly-
            # seeded room is now on an orphaned, never-persisted lineage
            # that no future checkpoint replay could ever integrate
            # updates against (confirmed in review) — reseed onto
            # whichever snapshot actually won instead, same rehydration
            # path as the normal "already has a snapshot" case, so every
            # process converges on one lineage regardless of who got here
            # first.
            rehydrate = await asyncio.to_thread(_read_snapshot_for_rehydrate, sess.id)
            assert rehydrate is not None  # we just lost to someone else's write of it
            sess_ck, since = rehydrate
            assert sess_ck.ydoc_snapshot is not None
            winner_doc = Doc()
            winner_doc.apply_update(sess_ck.ydoc_snapshot)
            for u in since.updates:
                try:
                    winner_doc.apply_update(u.update_payload)
                except Exception:
                    log.exception(
                        "coedit ws: session %s seq %d update failed to apply during"
                        " race-loss rehydrate; skipping",
                        sess.id,
                        u.seq,
                    )
            base_body = sess_ck.ydoc_snapshot_body
            coedit_room.reseed(room, winner_doc.get_update(), base_body, sess.base_sha)
        return room
    except (Exception, asyncio.CancelledError):
        # Any *other* failure here (e.g. _read_snapshot_for_rehydrate
        # raising on a busy checkpoint_lock), *including* this task being
        # cancelled (a client disconnect mid-``await`` races this exact
        # window — ``CancelledError`` derives from ``BaseException``, not
        # ``Exception``, so a bare ``except Exception`` silently let it
        # through uncleaned; confirmed via repro) — _connect_sync already
        # registered this user as a participant and broadcast their join;
        # undo both before letting the exception propagate, or they're
        # stuck as a phantom participant forever (confirmed in review —
        # this exact path had no cleanup at all before this fix). Bare
        # ``raise`` re-raises whatever was actually caught, so a
        # cancellation still actually cancels the task afterward.
        await asyncio.to_thread(coedit.leave, sess.id, user.id)
        await asyncio.to_thread(coedit_channel.broadcast_presence, sess.id)
        raise


@router.websocket("/ws")
async def ws(websocket: WebSocket, path: str, user: User = Depends(require_user_ws)) -> None:
    """``path`` is a query param (``?path=...``), not a URL segment — this
    app owns both ends of the connection URL, so there's no need for
    ``y-websocket``'s ``serverUrl/roomname`` convention.

    Joining is opening the page — presence + the live document — so
    connecting requires only read; see the module docstring for the write
    gate. This process's existing room (if it holds one) is adopted as-is.
    Otherwise — a second worker process, a restart, a rolling deploy, or
    truly the session's first-ever connection anywhere — the room is
    rebuilt from durable state: rehydrated from
    ``(ydoc_snapshot, coedit_updates)`` (the same replay
    ``coedit_checkpoint.py``'s engine does) if the session already has a
    snapshot, or seeded fresh from the page's git HEAD only for a session
    that has never had one. Seeding from git unconditionally here used to
    silently diverge two processes' rooms onto incompatible CRDT lineages
    the moment more than one worker (the deployed default) or a restart was
    involved — confirmed in review: an update logged against one lineage
    is integrated by neither the other worker's room nor a later
    checkpoint replaying onto the persisted snapshot.
    """
    sess, can_write = await asyncio.to_thread(_connect_sync, path, user)
    room = coedit_room.get_room(sess.id)
    if room is None:
        room = await _resolve_room(sess, user, websocket)
        if room is None:
            return

    # `_connect_sync` already registered the participant (`coedit.join`)
    # before returning, so from here on a disconnect — including one during
    # `accept()` itself, which the client can trigger mid-handshake since
    # `_connect_sync`'s git/DB work takes measurable time — must still reach
    # `record_leave` to undo it. Otherwise the user is stuck as a phantom
    # participant forever, which also blocks the last-leave checkpoint
    # trigger from ever firing for this session.
    conn: coedit_channel.Connection | None = None
    try:
        await websocket.accept()
        conn = coedit_channel.connect(sess.id, user.id)
        participants = await asyncio.to_thread(_participants_out, sess.id)
        await websocket.send_json(
            JoinedFrame(
                session_id=sess.id,
                base_sha=room.base_sha,
                can_write=can_write,
                participants=participants,
            ).model_dump()
        )
        # Offer our state so the new client's Yjs provider can request
        # exactly what it's missing — the standard two-way sync handshake.
        # A personal query sent directly to this connection only, never
        # broadcast.
        await websocket.send_bytes(create_sync_message(room.doc))

        recv_task = asyncio.create_task(
            _recv_loop(websocket, conn.queue, sess.id, user, room, can_write)
        )
        send_task = asyncio.create_task(_send_loop(websocket, conn, sess.id, user.id))
        done, pending = await asyncio.wait(
            {recv_task, send_task}, return_when=asyncio.FIRST_COMPLETED
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
        # The leave must survive this task being cancelled (server shutdown;
        # the test portal tearing down the connection). A cancelled `await`
        # is not enough: cancellation can land inside record_leave's own
        # first asyncio.to_thread call before that executor item is even
        # picked up — cancelling a not-yet-started future removes it from
        # the queue — and the cancellation can land at any moment, so no
        # up-front check closes the window. On that path, hand the leave to
        # the coedit queue instead: a synchronous enqueue nothing can
        # cancel, durable across the shutdown that caused it. `record_leave`
        # is idempotent, so the rare both-ran overlap is safe.
        try:
            await record_leave(sess.id, user.id)
        except asyncio.CancelledError:
            try:
                leave_coedit_session(sess.id, user.id)  # enqueue, no await
            except Exception:
                log.exception(
                    "coedit: queued-leave fallback failed for session %s", sess.id
                )
            raise
        log.info("coedit ws closed session=%s user=%s", sess.id, user.id)

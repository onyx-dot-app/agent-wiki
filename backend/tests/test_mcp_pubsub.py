"""Unit tests for the FastAPI SSE writer's pubsub seam.

Two responsibilities live here that the higher-level
``test_mcp_server_subscriptions`` file can't reach without driving an
actual streaming HTTP response:

1. The async-consumer path — ``register_async_consumer`` /
   ``drain_async`` — including the cross-thread ``call_soon_threadsafe``
   bridge used when a publisher on a worker thread hands work to the
   SSE writer's asyncio loop.

2. The SSE handler's disconnect-cleanup contract — the ``finally``
   block in ``transport_sse`` must call ``mcp_session.drop(sess_id)``
   (which in turn calls ``pubsub.forget``) when the response generator
   is closed.

Both are exercised directly so we don't pay for an SSE transport mock,
and so flakiness from streaming-response timing is eliminated.
"""
from __future__ import annotations

import asyncio
import threading
from typing import AsyncGenerator, cast

from fastapi import Request
from fastapi.responses import StreamingResponse

from app.auth import User
from app.mcp_server import pubsub as mcp_pubsub
from app.mcp_server import session as mcp_session
from app.models.wiki import ChangeKind

# Per-test pubsub/session reset is handled by the autouse ``_reset_mcp_state``
# fixture in ``tests/conftest.py``.


# --------------------------------------------------------------------------- #
# Async consumer seam                                                         #
# --------------------------------------------------------------------------- #


def test_drain_async_returns_published_notification(tmp_db):
    """The happy path: an item put on the async queue from inside the
    loop is returned by ``drain_async``.

    Needs ``tmp_db``: ``register_async_consumer`` rehydrates the
    session's persistent subscriptions from Postgres at stream open."""
    async def run() -> None:
        q = mcp_pubsub.register_async_consumer("s1")
        q.put_nowait(
            mcp_pubsub.Notification(method="test/method", params={"x": 1})
        )
        notif = await mcp_pubsub.drain_async(q, timeout=1.0)
        assert notif is not None
        assert notif.method == "test/method"
        assert notif.params == {"x": 1}

    asyncio.run(run())


def test_drain_async_returns_none_on_timeout(tmp_db):
    """Nothing queued → returns None after the timeout fires. The SSE
    writer relies on this to emit a heartbeat instead of parking the
    request indefinitely. ``tmp_db`` because ``register_async_consumer``
    rehydrates from Postgres."""
    async def run() -> None:
        q = mcp_pubsub.register_async_consumer("s1")
        notif = await mcp_pubsub.drain_async(q, timeout=0.05)
        assert notif is None

    asyncio.run(run())


def test_cross_thread_publish_reaches_async_consumer(tmp_repo):
    """The production hot path: a publisher on a different thread (a
    task worker, the LISTEN bridge) calls ``publish_doc_update`` and
    the SSE writer's ``drain_async`` wakes up with the notification.

    We seed a real user + ACL so ``_should_deliver`` returns True; the
    subscriber would otherwise be silently dropped before delivery.
    """
    from tests._seed import seed_user
    from app.wiki import git as wiki_git

    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\n", "seed", author=None)
    user = User(id=uid, email="u1@x.com")
    sess = mcp_session.create(user)
    sess_id = sess.id
    mcp_pubsub.subscribe_doc(sess_id, "page.md")

    async def run() -> None:
        q = mcp_pubsub.register_async_consumer(sess_id)
        # Publish from a non-loop thread so we exercise the
        # call_soon_threadsafe bridge in ``_push_async``.
        t = threading.Thread(
            target=mcp_pubsub.publish_doc_update,
            args=("page.md", "shadetail", "edit"),
        )
        t.start()
        notif = await mcp_pubsub.drain_async(q, timeout=2.0)
        t.join(timeout=1.0)
        assert notif is not None
        assert notif.method == "notifications/resources/updated"
        assert notif.params["uri"] == "wiki:///page.md"
        assert notif.params["sha"] == "shadetail"
        assert notif.params["changeKind"] == "edit"

    asyncio.run(run())


def test_register_async_consumer_drains_pending_sync_items(tmp_repo):
    """If a publish lands in the sync queue before the SSE writer opens
    (subscribe-then-publish-then-stream race), the registration must
    drain those items into the new async queue so the first
    ``drain_async`` sees them."""
    from tests._seed import seed_user

    uid = seed_user(uid="u1", email="u1@x.com")
    user = User(id=uid, email="u1@x.com")
    sess = mcp_session.create(user)
    sess_id = sess.id

    async def run() -> None:
        mcp_pubsub.subscribe_doc(sess_id, "page.md")  # creates the sync queue
        sync_q = mcp_pubsub.queue_for(sess_id)
        sync_q.put(
            mcp_pubsub.Notification(method="queued/before/stream", params={})
        )
        q = mcp_pubsub.register_async_consumer(sess_id)
        notif = await mcp_pubsub.drain_async(q, timeout=0.5)
        assert notif is not None
        assert notif.method == "queued/before/stream"

    asyncio.run(run())


def test_publish_with_live_async_consumer_skips_sync_queue(tmp_repo):
    """While an async consumer is registered, a publish must land only
    on the async queue. Double-writing to the sync queue leaked memory
    (nothing drains it during the stream) and replayed already-delivered
    notifications as duplicates when ``register_async_consumer`` drained
    it on the next reconnect."""
    from tests._seed import seed_user
    from app.wiki import git as wiki_git

    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\n", "seed", author=None)
    user = User(id=uid, email="u1@x.com")
    sess = mcp_session.create(user)
    sess_id = sess.id
    mcp_pubsub.subscribe_doc(sess_id, "page.md")

    async def run() -> None:
        q = mcp_pubsub.register_async_consumer(sess_id)
        mcp_pubsub.publish_doc_update("page.md", "sha1", ChangeKind.EDIT)
        notif = await mcp_pubsub.drain_async(q, timeout=2.0)
        assert notif is not None
        # The sync queue must NOT hold a copy of the same notification.
        assert mcp_pubsub.queue_for(sess_id).empty()
        # And a reconnect must not replay anything.
        q2 = mcp_pubsub.register_async_consumer(sess_id)
        assert await mcp_pubsub.drain_async(q2, timeout=0.05) is None

    asyncio.run(run())


def test_publish_without_async_consumer_parks_on_sync_queue(tmp_repo):
    """No SSE writer registered → the notification parks on the sync
    queue so ``register_async_consumer`` can drain it at stream open
    (and ``stale_paths`` can peek it meanwhile)."""
    from tests._seed import seed_user
    from app.wiki import git as wiki_git

    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\n", "seed", author=None)
    user = User(id=uid, email="u1@x.com")
    sess = mcp_session.create(user)
    sess_id = sess.id
    mcp_pubsub.subscribe_doc(sess_id, "page.md")

    mcp_pubsub.publish_doc_update("page.md", "sha1", ChangeKind.EDIT)
    notif = mcp_pubsub.drain_blocking(sess_id, timeout=1.0)
    assert notif is not None
    assert notif.params["uri"] == "wiki:///page.md"


def test_dispatch_skips_self_originated_notify(tmp_repo):
    """The LISTEN bridge must drop payloads stamped with this process's
    own origin tag — local delivery already happened at publish time,
    so re-publishing the echo would hand subscribers a duplicate."""
    import json

    from tests._seed import seed_user
    from app.wiki import git as wiki_git

    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\n", "seed", author=None)
    user = User(id=uid, email="u1@x.com")
    sess = mcp_session.create(user)
    sess_id = sess.id
    mcp_pubsub.subscribe_doc(sess_id, "page.md")

    payload = {"kind": "update", "rel": "page.md", "sha": "sha1", "change_kind": "edit"}

    # Self-originated → dropped, nothing delivered.
    mcp_pubsub._dispatch_notify_payload(
        json.dumps({**payload, "origin": mcp_pubsub._PROCESS_ORIGIN})
    )
    assert mcp_pubsub.drain_blocking(sess_id, timeout=0.05) is None

    # Foreign origin (another replica / the worker) → delivered.
    mcp_pubsub._dispatch_notify_payload(
        json.dumps({**payload, "origin": "some-other-process"})
    )
    notif = mcp_pubsub.drain_blocking(sess_id, timeout=1.0)
    assert notif is not None
    assert notif.params["uri"] == "wiki:///page.md"


# --------------------------------------------------------------------------- #
# DB-backed fan-out — no sticky-LB requirement                                #
# --------------------------------------------------------------------------- #


def test_subscribe_on_other_replica_reaches_local_stream(tmp_repo):
    """A ``resources/subscribe`` handled by a *different* replica only
    writes the DB row — this process's in-memory index never hears
    about it. Fan-out must still deliver to the live SSE stream here,
    because the subscription table (not the local index) decides who is
    subscribed."""
    from app.db import models as orm
    from app.db.session import session as db_session
    from tests._seed import seed_user
    from app.wiki import git as wiki_git

    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\n", "seed", author=None)
    user = User(id=uid, email="u1@x.com")
    sess = mcp_session.create(user)
    sess_id = sess.id

    async def run() -> None:
        # Stream opens first — rehydrates an empty subscription set.
        q = mcp_pubsub.register_async_consumer(sess_id)
        # Mid-stream subscribe lands on another replica: DB row only,
        # no local subscribe_doc call.
        with db_session() as s:
            s.add(orm.McpPathSubscription(session_id=sess_id, rel_path="page.md"))
        mcp_pubsub.publish_doc_update("page.md", "sha1", ChangeKind.EDIT)
        notif = await mcp_pubsub.drain_async(q, timeout=2.0)
        assert notif is not None
        assert notif.params["uri"] == "wiki:///page.md"

    asyncio.run(run())


def test_unsubscribe_on_other_replica_stops_local_delivery(tmp_repo):
    """The mirror case: an unsubscribe handled elsewhere removes the DB
    row but leaves this process's rehydrated index stale. The stale
    index entry must not produce a delivery."""
    from sqlalchemy import delete as sa_delete

    from app.db import models as orm
    from app.db.session import execute_dml, session as db_session
    from tests._seed import seed_user
    from app.wiki import git as wiki_git

    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\n", "seed", author=None)
    user = User(id=uid, email="u1@x.com")
    sess = mcp_session.create(user)
    sess_id = sess.id
    mcp_pubsub.subscribe_doc(sess_id, "page.md")

    async def run() -> None:
        q = mcp_pubsub.register_async_consumer(sess_id)
        # Unsubscribe lands on another replica: DB row gone, local
        # index still lists the path.
        with db_session() as s:
            execute_dml(
                s,
                sa_delete(orm.McpPathSubscription).where(
                    orm.McpPathSubscription.session_id == sess_id
                ),
            )
        mcp_pubsub.publish_doc_update("page.md", "sha1", ChangeKind.EDIT)
        assert await mcp_pubsub.drain_async(q, timeout=0.1) is None

    asyncio.run(run())


def test_parked_sync_queue_is_bounded_drop_oldest():
    """A session whose stream lives (or never opens) on another replica
    parks notifications here. The queue must cap at ``_SYNC_QUEUE_MAX``
    and shed the oldest entries, not grow without bound."""
    sess_id = "s-parked"
    total = mcp_pubsub._SYNC_QUEUE_MAX + 10
    for i in range(total):
        mcp_pubsub._deliver(
            sess_id,
            mcp_pubsub.Notification(method="test/seq", params={"i": i}),
        )
    q = mcp_pubsub.queue_for(sess_id)
    assert q.qsize() == mcp_pubsub._SYNC_QUEUE_MAX
    # Oldest were dropped: the head of the queue is the 11th publish.
    first = q.get_nowait()
    assert first.params["i"] == total - mcp_pubsub._SYNC_QUEUE_MAX


# --------------------------------------------------------------------------- #
# SSE handler cleanup contract                                                #
# --------------------------------------------------------------------------- #


def test_sse_handler_finally_drops_session_on_close(tmp_repo):
    """Drive ``transport_sse`` directly: open the stream, let the
    generator register its consumer and start awaiting, then close it.
    The generator's ``finally`` must call ``mcp_session.drop(sess_id)``,
    which evicts the session from the local cache and forgets the
    in-memory subscription state. The persistent ``mcp_sessions`` row
    remains so the client's ``Mcp-Session-Id`` is still recognized on
    reconnect — that's the durability invariant the test asserts on
    below.
    """
    from app.api.mcp_server import transport_sse
    from tests._seed import seed_user

    uid = seed_user(uid="u1", email="u1@x.com")
    user = User(id=uid, email="u1@x.com")
    sess = mcp_session.create(user)
    mcp_session.mark_initialized(sess.id)
    sess_id = sess.id
    mcp_pubsub.subscribe_doc(sess_id, "page.md")

    class FakeRequest:
        def __init__(self, sess_id: str) -> None:
            self.headers = {"Mcp-Session-Id": sess_id}

        async def is_disconnected(self) -> bool:
            return False

    from app.auth.deps import BearerPrincipal

    principal = BearerPrincipal(user=user, agent_name="test-agent")

    async def run() -> None:
        resp = await transport_sse(
            cast(Request, FakeRequest(sess_id)), principal=principal,
        )
        body = cast(
            AsyncGenerator[bytes, None],
            cast(StreamingResponse, resp).body_iterator,
        )
        # Pump one iteration so the generator enters its loop and the
        # heartbeat path yields. Use a short heartbeat by patching the
        # constant locally so we don't wait the default 15s.
        from app.api import mcp_server as api_mcp

        api_mcp._SSE_HEARTBEAT_SECONDS = 0.05
        chunk = await body.__anext__()
        assert chunk == b": keepalive\n\n"
        await body.aclose()

    asyncio.run(run())

    # Persistent session row survives the SSE close so the client can
    # reconnect with the same Mcp-Session-Id.
    surviving = mcp_session.get(sess_id)
    assert surviving is not None
    assert surviving.id == sess_id
    # But the in-memory subscription index for this session is cleared,
    # so a stale publish doesn't try to deliver into a closed queue.
    assert not mcp_pubsub.is_subscribed(sess_id, "page.md")

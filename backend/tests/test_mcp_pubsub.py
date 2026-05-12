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

# Per-test pubsub/session reset is handled by the autouse ``_reset_mcp_state``
# fixture in ``tests/conftest.py``.


# --------------------------------------------------------------------------- #
# Async consumer seam                                                         #
# --------------------------------------------------------------------------- #


def test_drain_async_returns_published_notification():
    """The happy path: an item put on the async queue from inside the
    loop is returned by ``drain_async``."""
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


def test_drain_async_returns_none_on_timeout():
    """Nothing queued → returns None after the timeout fires. The SSE
    writer relies on this to emit a heartbeat instead of parking the
    request indefinitely."""
    async def run() -> None:
        q = mcp_pubsub.register_async_consumer("s1")
        notif = await mcp_pubsub.drain_async(q, timeout=0.05)
        assert notif is None

    asyncio.run(run())


def test_cross_thread_publish_reaches_async_consumer(tmp_repo):
    """The production hot path: a publisher on a different thread (a
    pgmq worker, the LISTEN bridge) calls ``publish_doc_update`` and
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


def test_register_async_consumer_drains_pending_sync_items():
    """If a publish lands in the sync queue before the SSE writer opens
    (subscribe-then-publish-then-stream race), the registration must
    drain those items into the new async queue so the first
    ``drain_async`` sees them."""
    async def run() -> None:
        mcp_pubsub.subscribe_doc("s1", "page.md")  # creates the sync queue
        sync_q = mcp_pubsub.queue_for("s1")
        sync_q.put(
            mcp_pubsub.Notification(method="queued/before/stream", params={})
        )
        q = mcp_pubsub.register_async_consumer("s1")
        notif = await mcp_pubsub.drain_async(q, timeout=0.5)
        assert notif is not None
        assert notif.method == "queued/before/stream"

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# SSE handler cleanup contract                                                #
# --------------------------------------------------------------------------- #


def test_sse_handler_finally_drops_session_on_close(tmp_repo):
    """Drive ``transport_sse`` directly: open the stream, let the
    generator register its consumer and start awaiting, then close it.
    The generator's ``finally`` must call ``mcp_session.drop(sess_id)``,
    which clears the session row and forgets the subscription.
    """
    from app.api.mcp_server import transport_sse
    from tests._seed import seed_user

    uid = seed_user(uid="u1", email="u1@x.com")
    user = User(id=uid, email="u1@x.com")
    sess = mcp_session.create(user)
    sess.initialized = True
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

    assert mcp_session.get(sess_id) is None
    assert not mcp_pubsub.is_subscribed(sess_id, "page.md")

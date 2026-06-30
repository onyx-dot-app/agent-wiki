"""Co-edit live channel (app/wiki/coedit_channel.py) — the in-memory
connection registry, fan-out, and the cross-process ``handle_remote`` path.

Async bits are driven through ``asyncio.run`` since the suite has no
pytest-asyncio; ``connect`` needs a running loop.
"""
from __future__ import annotations

import asyncio

from app.wiki import coedit_channel


def _drain_now(queue) -> dict | None:
    # Frames are scheduled via call_soon_threadsafe; a zero-ish timeout still
    # lets the scheduled callback run before we read.
    return asyncio.run(coedit_channel.drain(queue, 0.5))


def test_connect_publish_delivers_to_connection():
    async def scenario():
        coedit_channel.reset_for_tests()
        _conn, queue = coedit_channel.connect(1, "usr_a")
        coedit_channel._deliver_local(1, {"type": "presence", "n": 1})
        return await coedit_channel.drain(queue, 0.5)

    frame = asyncio.run(scenario())
    assert frame == {"type": "presence", "n": 1}


def test_fan_out_to_all_connections_in_session():
    async def scenario():
        coedit_channel.reset_for_tests()
        _a, qa = coedit_channel.connect(7, "usr_a")
        _b, qb = coedit_channel.connect(7, "usr_b")
        coedit_channel._deliver_local(7, {"hello": "world"})
        return (await coedit_channel.drain(qa, 0.5), await coedit_channel.drain(qb, 0.5))

    fa, fb = asyncio.run(scenario())
    assert fa == {"hello": "world"}
    assert fb == {"hello": "world"}


def test_other_session_does_not_receive():
    async def scenario():
        coedit_channel.reset_for_tests()
        _a, qa = coedit_channel.connect(1, "usr_a")
        coedit_channel._deliver_local(2, {"to": "other"})
        return await coedit_channel.drain(qa, 0.2)

    assert asyncio.run(scenario()) is None  # timed out — nothing delivered


def test_disconnect_stops_delivery_and_clears_state():
    async def scenario():
        coedit_channel.reset_for_tests()
        conn, queue = coedit_channel.connect(3, "usr_a")
        coedit_channel.disconnect(conn)
        coedit_channel._deliver_local(3, {"x": 1})
        return await coedit_channel.drain(queue, 0.2)

    assert asyncio.run(scenario()) is None


def test_user_still_connected_tracks_multiple_tabs():
    async def scenario():
        coedit_channel.reset_for_tests()
        c1, _ = coedit_channel.connect(5, "usr_a")
        c2, _ = coedit_channel.connect(5, "usr_a")
        first = coedit_channel.user_still_connected(5, "usr_a")
        coedit_channel.disconnect(c1)
        # One tab closed, the other still open → user is still present.
        mid = coedit_channel.user_still_connected(5, "usr_a")
        coedit_channel.disconnect(c2)
        last = coedit_channel.user_still_connected(5, "usr_a")
        return first, mid, last

    first, mid, last = asyncio.run(scenario())
    assert first is True
    assert mid is True
    assert last is False


def test_handle_remote_delivers_locally():
    async def scenario():
        coedit_channel.reset_for_tests()
        _c, queue = coedit_channel.connect(9, "usr_a")
        coedit_channel.handle_remote(
            {"coedit_session_id": 9, "frame": {"type": "presence", "via": "notify"}}
        )
        return await coedit_channel.drain(queue, 0.5)

    assert asyncio.run(scenario()) == {"type": "presence", "via": "notify"}

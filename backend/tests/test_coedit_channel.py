"""Co-edit live channel (app/wiki/coedit_channel.py) — the in-memory
connection registry, fan-out, and the cross-process ``handle_remote`` path.

Synchronous (thread-per-connection + ``queue.Queue``), so the tests are plain
puts and gets — no event loop.
"""
from __future__ import annotations

from app.wiki import coedit_channel


def test_connect_publish_delivers_to_connection():
    coedit_channel.reset_for_tests()
    _conn, q = coedit_channel.connect(1, "usr_a")
    coedit_channel._deliver_local(1, {"type": "presence", "n": 1})
    assert coedit_channel.drain(q, 0.5) == {"type": "presence", "n": 1}


def test_fan_out_to_all_connections_in_session():
    coedit_channel.reset_for_tests()
    _a, qa = coedit_channel.connect(7, "usr_a")
    _b, qb = coedit_channel.connect(7, "usr_b")
    coedit_channel._deliver_local(7, {"hello": "world"})
    assert coedit_channel.drain(qa, 0.5) == {"hello": "world"}
    assert coedit_channel.drain(qb, 0.5) == {"hello": "world"}


def test_other_session_does_not_receive():
    coedit_channel.reset_for_tests()
    _a, qa = coedit_channel.connect(1, "usr_a")
    coedit_channel._deliver_local(2, {"to": "other"})
    assert coedit_channel.drain(qa, 0.1) is None  # timed out — nothing delivered


def test_disconnect_stops_delivery_and_clears_state():
    coedit_channel.reset_for_tests()
    conn, q = coedit_channel.connect(3, "usr_a")
    coedit_channel.disconnect(conn)
    coedit_channel._deliver_local(3, {"x": 1})
    assert coedit_channel.drain(q, 0.1) is None


def test_user_still_connected_tracks_multiple_tabs():
    coedit_channel.reset_for_tests()
    c1, _ = coedit_channel.connect(5, "usr_a")
    c2, _ = coedit_channel.connect(5, "usr_a")
    assert coedit_channel.user_still_connected(5, "usr_a") is True
    coedit_channel.disconnect(c1)
    # One tab closed, the other still open → user is still present.
    assert coedit_channel.user_still_connected(5, "usr_a") is True
    coedit_channel.disconnect(c2)
    assert coedit_channel.user_still_connected(5, "usr_a") is False


def test_handle_remote_delivers_locally():
    coedit_channel.reset_for_tests()
    _c, q = coedit_channel.connect(9, "usr_a")
    coedit_channel.handle_remote(
        {"coedit_session_id": 9, "frame": {"type": "presence", "via": "notify"}}
    )
    assert coedit_channel.drain(q, 0.5) == {"type": "presence", "via": "notify"}

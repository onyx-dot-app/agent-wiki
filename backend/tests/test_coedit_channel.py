"""Co-edit live channel (app/wiki/coedit_channel.py) — the in-memory
connection registry, fan-out, and the cross-process ``handle_remote`` path.

Synchronous (thread-per-connection + ``queue.Queue``), so the tests are plain
puts and gets — no event loop.
"""
from __future__ import annotations

from app.wiki import coedit, coedit_channel


def test_connect_publish_delivers_to_connection():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(1, "usr_a")
    coedit_channel._deliver_local(1, {"type": "presence", "n": 1})
    assert coedit_channel.drain(conn.queue, 0.5) == {"type": "presence", "n": 1}


def test_fan_out_to_all_connections_in_session():
    coedit_channel.reset_for_tests()
    a = coedit_channel.connect(7, "usr_a")
    b = coedit_channel.connect(7, "usr_b")
    coedit_channel._deliver_local(7, {"hello": "world"})
    assert coedit_channel.drain(a.queue, 0.5) == {"hello": "world"}
    assert coedit_channel.drain(b.queue, 0.5) == {"hello": "world"}


def test_other_session_does_not_receive():
    coedit_channel.reset_for_tests()
    a = coedit_channel.connect(1, "usr_a")
    coedit_channel._deliver_local(2, {"to": "other"})
    assert coedit_channel.drain(a.queue, 0.1) is None  # timed out — nothing delivered


def test_disconnect_stops_delivery_and_clears_state():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(3, "usr_a")
    coedit_channel.disconnect(conn.id)
    coedit_channel._deliver_local(3, {"x": 1})
    assert coedit_channel.drain(conn.queue, 0.1) is None


def test_user_still_connected_tracks_multiple_tabs():
    coedit_channel.reset_for_tests()
    c1 = coedit_channel.connect(5, "usr_a")
    c2 = coedit_channel.connect(5, "usr_a")
    assert coedit_channel.user_still_connected(5, "usr_a") is True
    coedit_channel.disconnect(c1.id)
    # One tab closed, the other still open → user is still present.
    assert coedit_channel.user_still_connected(5, "usr_a") is True
    coedit_channel.disconnect(c2.id)
    assert coedit_channel.user_still_connected(5, "usr_a") is False


def _change(frm: int, to: int, insert: str) -> coedit.Change:
    return coedit.Change.model_validate({"from": frm, "to": to, "insert": insert})


def test_broadcast_op_delivers_op_frame():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(4, "usr_a")
    coedit_channel.broadcast_op(4, 3, [_change(0, 1, "x")], "usr_a")
    frame = coedit_channel.drain(conn.queue, 0.5)
    assert frame == {
        "type": "op",
        "session_id": 4,
        "version": 3,
        "changes": [{"from": 0, "to": 1, "insert": "x"}],
        "author": "usr_a",
    }


def test_broadcast_op_oversized_falls_back_to_resync():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(4, "usr_a")
    big = "z" * 8000  # payload exceeds the NOTIFY cap → resync signal instead
    coedit_channel.broadcast_op(4, 5, [_change(0, 0, big)], "usr_a")
    frame = coedit_channel.drain(conn.queue, 0.5)
    assert frame == {"type": "resync", "session_id": 4, "version": 5}


def test_broadcast_cursor_delivers_selection_frame_to_peers():
    coedit_channel.reset_for_tests()
    a = coedit_channel.connect(6, "usr_a")
    b = coedit_channel.connect(6, "usr_b")  # a peer in the same session
    coedit_channel.broadcast_cursor(
        6, user_id="usr_a", user_display="Ada", anchor=3, head=10, typing=True
    )
    expected = {
        "type": "cursor",
        "session_id": 6,
        "user_id": "usr_a",
        "user_display": "Ada",
        "anchor": 3,
        "head": 10,
        "typing": True,
    }
    # The peer receives it — not just the sender's own connection.
    assert coedit_channel.drain(b.queue, 0.5) == expected
    assert coedit_channel.drain(a.queue, 0.5) == expected


def test_handle_remote_delivers_locally():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(9, "usr_a")
    coedit_channel.handle_remote(
        {"coedit_session_id": 9, "frame": {"type": "presence", "via": "notify"}}
    )
    assert coedit_channel.drain(conn.queue, 0.5) == {"type": "presence", "via": "notify"}

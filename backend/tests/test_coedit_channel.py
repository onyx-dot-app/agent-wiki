"""Co-edit live channel (app/wiki/coedit_channel.py) — the in-memory
connection registry, fan-out, and the cross-process ``handle_remote`` path.

Synchronous (thread-per-connection + ``queue.Queue``), so the tests are plain
puts and gets — no event loop.
"""
from __future__ import annotations

from app.wiki import coedit, coedit_channel


def test_connect_publish_delivers_to_connection():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(1)
    coedit_channel._deliver_local(1, {"type": "presence", "n": 1})
    assert coedit_channel.drain(conn.queue, 0.5) == {"type": "presence", "n": 1}


def test_fan_out_to_all_connections_in_session():
    coedit_channel.reset_for_tests()
    a = coedit_channel.connect(7)
    b = coedit_channel.connect(7)
    coedit_channel._deliver_local(7, {"hello": "world"})
    assert coedit_channel.drain(a.queue, 0.5) == {"hello": "world"}
    assert coedit_channel.drain(b.queue, 0.5) == {"hello": "world"}


def test_other_session_does_not_receive():
    coedit_channel.reset_for_tests()
    a = coedit_channel.connect(1)
    coedit_channel._deliver_local(2, {"to": "other"})
    assert coedit_channel.drain(a.queue, 0.1) is None  # timed out — nothing delivered


def test_disconnect_stops_delivery_and_clears_state():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(3)
    coedit_channel.disconnect(conn.id)
    coedit_channel._deliver_local(3, {"x": 1})
    assert coedit_channel.drain(conn.queue, 0.1) is None


def _change(frm: int, to: int, insert: str) -> coedit.Change:
    return coedit.Change.model_validate({"from": frm, "to": to, "insert": insert})


def test_broadcast_op_delivers_op_frame():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(4)
    coedit_channel.broadcast_op(4, 3, [_change(0, 1, "x")], "usr_a", client_id="cli_1")
    frame = coedit_channel.drain(conn.queue, 0.5)
    assert frame == {
        "type": "op",
        "session_id": 4,
        "version": 3,
        "changes": [{"from": 0, "to": 1, "insert": "x"}],
        "author": "usr_a",
        "client_id": "cli_1",
        "caret_seq": None,
    }


def test_broadcast_op_oversized_falls_back_to_resync():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(4)
    big = "z" * 8000  # payload exceeds the NOTIFY cap → resync signal instead
    coedit_channel.broadcast_op(4, 5, [_change(0, 0, big)], "usr_a")
    frame = coedit_channel.drain(conn.queue, 0.5)
    assert frame == {"type": "resync", "session_id": 4, "version": 5}


def test_broadcast_cursor_delivers_selection_frame_to_peers():
    coedit_channel.reset_for_tests()
    a = coedit_channel.connect(6)
    b = coedit_channel.connect(6)  # a peer in the same session
    coedit_channel.broadcast_cursor(
        6, user_id="usr_a", user_display="Ada", anchor=3, head=10, typing=True, seq=7
    )
    expected = {
        "type": "cursor",
        "session_id": 6,
        "user_id": "usr_a",
        "user_display": "Ada",
        "anchor": 3,
        "head": 10,
        "typing": True,
        "seq": 7,
    }
    # The peer receives it — not just the sender's own connection.
    assert coedit_channel.drain(b.queue, 0.5) == expected
    assert coedit_channel.drain(a.queue, 0.5) == expected


def test_broadcast_cursor_cleared_delivers_null_positions():
    # A cleared caret (editor blur / tab hidden) rides the same frame with
    # null anchor/head — peers drop the caret on it.
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(6)
    coedit_channel.broadcast_cursor(
        6,
        user_id="usr_a",
        user_display="Ada",
        anchor=None,
        head=None,
        typing=False,
        seq=8,
    )
    assert coedit_channel.drain(conn.queue, 0.5) == {
        "type": "cursor",
        "session_id": 6,
        "user_id": "usr_a",
        "user_display": "Ada",
        "anchor": None,
        "head": None,
        "typing": False,
        "seq": 8,
    }


def test_handle_remote_delivers_locally():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(9)
    coedit_channel.handle_remote(
        {"coedit_session_id": 9, "frame": {"type": "presence", "via": "notify"}}
    )
    assert coedit_channel.drain(conn.queue, 0.5) == {"type": "presence", "via": "notify"}

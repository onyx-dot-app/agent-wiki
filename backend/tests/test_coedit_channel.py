"""Co-edit live channel (app/wiki/coedit_channel.py) — the in-memory
connection registry, fan-out, and the cross-process ``_handle_remote_*``
paths.

Synchronous (thread-per-connection + ``queue.Queue``), so the tests are plain
puts and gets — no event loop, no DB fixtures. ``broadcast_yjs``/
``publish_control`` also call ``bus.emit`` for cross-process delivery; with
no DB configured here that NOTIFY best-effort-fails and is swallowed (see
``bus.emit``'s own docstring), so it doesn't interfere with asserting on the
local delivery path these tests care about.
"""
from __future__ import annotations

import base64

from app.wiki import coedit_channel


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


def test_publish_control_delivers_to_all_connections_in_session():
    coedit_channel.reset_for_tests()
    a = coedit_channel.connect(4, "usr_a")
    b = coedit_channel.connect(4, "usr_b")
    coedit_channel.publish_control(4, {"type": "resync", "session_id": 4})
    assert coedit_channel.drain(a.queue, 0.5) == {"type": "resync", "session_id": 4}
    assert coedit_channel.drain(b.queue, 0.5) == {"type": "resync", "session_id": 4}


def test_broadcast_yjs_delivers_bytes_frame_to_peers():
    coedit_channel.reset_for_tests()
    a = coedit_channel.connect(6, "usr_a")
    b = coedit_channel.connect(6, "usr_b")
    payload = b"\x00\x01hello"
    coedit_channel.broadcast_yjs(6, payload)
    # No origin-exclusion — the sender's own connection receives the echo
    # too (CRDT updates are idempotent; see broadcast_yjs's docstring).
    assert coedit_channel.drain(a.queue, 0.5) == coedit_channel.YjsBytes(payload=payload)
    assert coedit_channel.drain(b.queue, 0.5) == coedit_channel.YjsBytes(payload=payload)


def test_broadcast_yjs_other_session_does_not_receive():
    coedit_channel.reset_for_tests()
    a = coedit_channel.connect(1, "usr_a")
    coedit_channel.broadcast_yjs(2, b"\x00\x01other")
    assert coedit_channel.drain(a.queue, 0.1) is None


def test_handle_remote_control_delivers_locally():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(9, "usr_a")
    coedit_channel._handle_remote_control(
        {"session_id": 9, "frame": {"type": "presence", "via": "notify"}}
    )
    assert coedit_channel.drain(conn.queue, 0.5) == {"type": "presence", "via": "notify"}


def test_handle_remote_yjs_single_chunk_delivers_locally():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(9, "usr_a")
    payload = b"\x00\x01remote-update"
    coedit_channel._handle_remote_yjs(
        {
            "session_id": 9,
            "i": 0,
            "n": 1,
            "group": None,
            "chunk": base64.b64encode(payload).decode("ascii"),
        }
    )
    assert coedit_channel.drain(conn.queue, 0.5) == coedit_channel.YjsBytes(payload=payload)


def test_handle_remote_yjs_reassembles_chunks_in_any_order():
    coedit_channel.reset_for_tests()
    conn = coedit_channel.connect(9, "usr_a")
    payload = b"x" * 100
    b64 = base64.b64encode(payload).decode("ascii")
    third = len(b64) // 3
    chunks = [b64[:third], b64[third : 2 * third], b64[2 * third :]]
    group = "group-1"
    # Deliver out of order — reassembly is keyed by index, not arrival order.
    for i in (2, 0, 1):
        coedit_channel._handle_remote_yjs(
            {"session_id": 9, "i": i, "n": 3, "group": group, "chunk": chunks[i]}
        )
    assert coedit_channel.drain(conn.queue, 0.5) == coedit_channel.YjsBytes(payload=payload)
    # Reassembly buffer is cleared once complete.
    assert group not in coedit_channel._partial_chunks

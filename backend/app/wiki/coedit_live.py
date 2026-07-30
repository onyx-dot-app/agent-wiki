"""Short-lived `pycrdt.Doc`s built on demand from `(ydoc_snapshot, coedit_updates)`.

The server keeps no resident replica of a session's document. Every function
here builds a throwaway `Doc`, uses it, and drops it before returning — the
durable state is the snapshot plus the update log, and that is the only
representation.

Why no resident replica: a `Doc` the server holds is a CRDT replica, and Yjs
guarantees convergence only between replicas that exchange updates. A resident
replica that the server also mutates outside the protocol (restamping block
ids, splicing a markdown diff, reseeding) has no convergence guarantee at all,
so no amount of timing care makes it safe — the mutation simply never enters
the protocol. Building per call removes the second representation instead of
trying to keep two in step.

Thread affinity still applies, and it is why every function here is
self-contained. `Doc`/`Subscription` are PyO3 "unsendable" Rust types: a `Doc`
may only be touched from the thread that created it. So a `Doc` must never be
returned to a caller or stored — each function creates, uses and drops one
inside a single call, and callers reach them via `asyncio.to_thread` from the
WS route (see CLAUDE.md's WebSocket-route rule).

Cost, measured on pycrdt 0.14.1: a rebuild (`Doc()` + `apply_update(snapshot)`
+ replaying ~100 logged updates) is ~0.2–1.3 ms for 4 KB–140 KB pages, against
the 3–68 ms that seeding a doc from markdown used to cost at join. Validating
one client update is ~2 µs.
"""

from __future__ import annotations

import logging

from pycrdt import Doc, YMessageType, create_sync_message, handle_sync_message, read_message

from app.wiki import coedit
from app.wiki.markdown_yjs import reconstruct_body

log = logging.getLogger(__name__)


class SessionGone(Exception):
    """The session isn't active, or has no snapshot to rebuild from."""


def _load(session_id: int) -> tuple[Doc, int]:
    """Rebuild a session's document; returns it with the seq it is current as of.

    Private on purpose: the `Doc` must not leave the calling thread, so every
    caller in this module consumes it and drops it within its own function.
    """
    sess = coedit.get_session_for_checkpoint(session_id)
    if sess is None or sess.ydoc_snapshot is None:
        raise SessionGone(f"session {session_id} has no rebuildable state")
    doc = Doc()
    doc.apply_update(sess.ydoc_snapshot)
    since = coedit.updates_since(session_id, sess.ydoc_snapshot_seq)
    for u in since.updates:
        try:
            doc.apply_update(u.update_payload)
        except Exception:
            # A single unapplyable row must not sink the whole rebuild: the
            # rest of the log is still valid CRDT lineage. Logged loudly
            # because it means a row was written that the doc can't integrate.
            log.exception(
                "coedit live: session %s seq %d failed to apply during rebuild; skipping",
                session_id,
                u.seq,
            )
    return doc, since.head_seq if since.head_seq is not None else sess.ydoc_snapshot_seq


def validate_update(payload: bytes) -> bool:
    """True if ``payload`` is a Yjs update this server can integrate.

    Applied to a scratch `Doc` rather than the session's: the point is only to
    reject garbage before it reaches the durable log, and an update is
    integrable independent of what it is integrated into.
    """
    try:
        Doc().apply_update(payload)
        return True
    except Exception:
        return False


def sync_reply(session_id: int, payload: bytes) -> bytes | None:
    """Answer a client's sync message (the join handshake), or ``None``.

    Handles STEP1 (client sends its state vector, we reply with what it's
    missing) and STEP2/UPDATE the same way `pycrdt` does against a resident
    doc — the doc here is just built for the call.
    """
    doc, _seq = _load(session_id)
    return handle_sync_message(payload[1:], doc)


def initial_sync_message(session_id: int) -> bytes:
    """The server's own STEP1, sent on connect so the client sends us its diff."""
    doc, _seq = _load(session_id)
    return create_sync_message(doc)


def read_body(session_id: int) -> str | None:
    """The session's live document as markdown, for a session-aware page read.

    Any process can serve this now: it reads the durable log rather than a
    replica that happens to live in one worker. Under `--workers 2` the
    resident-room version could only answer for sessions its own worker held,
    and silently served committed HEAD for the rest.
    """
    try:
        doc, _seq = _load(session_id)
    except SessionGone:
        return None
    return reconstruct_body(doc)


def state_and_diff(session_id: int, new_body: str) -> tuple[bytes, int] | None:
    """Produce the update that moves the session's document to ``new_body``.

    Used by live-rebase: an out-of-band commit becomes an ordinary logged and
    broadcast delta, which commutes with concurrent appends, rather than a
    wholesale reseed that severs CRDT lineage. Returns ``(update_bytes, seq)``
    where ``seq`` is what the document was current as of when the diff was
    taken; ``None`` when the body already matches.
    """
    from app.wiki.markdown_splice import apply_markdown_diff  # noqa: PLC0415

    try:
        doc, seq = _load(session_id)
    except SessionGone:
        return None
    before = doc.get_state()
    if reconstruct_body(doc) == new_body:
        return None
    apply_markdown_diff(doc, new_body)
    return doc.get_update(before), seq


__all__ = [
    "SessionGone",
    "initial_sync_message",
    "read_body",
    "state_and_diff",
    "sync_reply",
    "validate_update",
    "YMessageType",
    "read_message",
]

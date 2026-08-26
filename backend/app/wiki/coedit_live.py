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

from pycrdt import (
    Doc,
    YMessageType,
    YSyncMessageType,
    create_sync_message,
    handle_sync_message,
    read_message,
)

from app.wiki import coedit
from app.wiki.git import merge_content
from app.wiki.markdown_splice import apply_markdown_diff, restamp_block_ids
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
        if u.lineage != sess.ydoc_lineage:
            # A leftover row from a replaced lineage — same guard as the
            # checkpoint engine's replay: integrating it would union the old
            # document's content into the current one.
            log.warning(
                "coedit live: session %s seq %d is from replaced lineage %d "
                "(current %d); skipping",
                session_id,
                u.seq,
                u.lineage,
                sess.ydoc_lineage,
            )
            continue
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


def _read_var_uint(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode one lib0 variable-length unsigned int (7 bits per byte, high
    bit = continuation) — the primitive a Yjs state vector is built from."""
    value = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, pos
        shift += 7


def state_vector_client_ids(sv: bytes) -> set[int]:
    """The client ids carrying a nonzero clock in an encoded Yjs state vector.

    A state vector is the compact "what I have" summary a client sends in
    SYNC_STEP1: a count, then ``(client_id, clock)`` varint pairs. The id set
    is what makes lineages comparable without content: two documents that
    ever exchanged updates share ids; two independently seeded documents
    share none.
    """
    ids: set[int] = set()
    count, pos = _read_var_uint(sv, 0)
    for _ in range(count):
        client, pos = _read_var_uint(sv, pos)
        clock, pos = _read_var_uint(sv, pos)
        if clock > 0:
            ids.add(client)
    return ids


def sync_reply(session_id: int, payload: bytes) -> tuple[bytes | None, bool]:
    """Answer a client's sync message (the join handshake); returns
    ``(reply, foreign)``.

    Handles STEP1 (client sends its state vector, we reply with what it's
    missing) and STEP2/UPDATE the same way `pycrdt` does against a resident
    doc — the doc here is just built for the call.

    ``foreign`` is True when a STEP1's state vector shares **no** client id
    with the session's document while both are non-empty. That is the
    signature of a replaced lineage: a document that ever synced with this
    session shares most of its ids, while a doc retained across a server
    reseed shares none — and letting it sync would union both documents'
    content into a duplicated page. The lineage-generation guard cannot catch
    this case when the sender reconnected *after* the reseed (its connection
    joined at the current generation; the foreignness is in the payload),
    which is exactly what a client that predates the ``resync_required``
    protocol does. The one false positive — a client that joined, never
    received server state, and typed before syncing — is refused too, which
    costs those keystrokes but can't corrupt; only pre-``resync_required``
    clients can reach that state.
    """
    doc, _seq = _load(session_id)
    sync = payload[1:]
    foreign = False
    if sync and sync[0] == YSyncMessageType.SYNC_STEP1:
        client_ids = state_vector_client_ids(read_message(sync[1:]))
        doc_ids = state_vector_client_ids(doc.get_state())
        foreign = bool(client_ids) and bool(doc_ids) and not (client_ids & doc_ids)
    return handle_sync_message(sync, doc), foreign


def initial_sync_message(session_id: int) -> bytes:
    """The server's own STEP1, sent on connect so the client sends us its diff."""
    doc, _seq = _load(session_id)
    return create_sync_message(doc)


def read_body(session_id: int) -> str | None:
    """The session's live document as markdown, for a session-aware page read.

    Any process can serve this: it reads the durable log rather than a replica
    that happens to live in one worker.
    """
    try:
        doc, _seq = _load(session_id)
    except SessionGone:
        return None
    return reconstruct_body(doc)


def rebase_delta(
    session_id: int, base_body: str, current_body: str
) -> tuple[bytes | None, str, bool] | None:
    """Fold an out-of-band commit into the session as an ordinary Yjs update.

    Returns ``(update_bytes, merged_body, clean)``; ``update_bytes`` is ``None``
    when the merge collapsed to what the document already had, and ``clean`` is
    ``False`` when the three-way merge overlapped (the caller hands those to the
    checkpoint engine's AI merge instead). ``None`` means the session is gone.

    One rebuild does the whole job — read the body, merge, apply the diff, emit
    the delta — because the ``Doc`` cannot leave this thread, and doing it in two
    calls would rebuild twice for no benefit.

    The delta is the point: an update commutes with whatever else clients are
    appending, so folding a commit in needs no compare-and-swap, no snapshot
    swap, and no reseed. A reseed would mint a fresh CRDT lineage, which is
    precisely what leaves concurrent edits unintegrable.
    """
    try:
        doc, _seq = _load(session_id)
    except SessionGone:
        return None
    ours = reconstruct_body(doc)
    mr = merge_content(base_body, current_body, ours)
    if not mr.clean:
        return None, mr.merged, False
    if mr.merged == ours:
        return None, mr.merged, True
    before = doc.get_state()
    if not apply_markdown_diff(doc, ours, mr.merged):
        # The document's children don't correspond 1:1 to a fresh parse of
        # their own body, so there's no safe block pairing to splice along
        # (see `apply_markdown_diff`). Reported as unclean so the caller hands
        # it to the checkpoint engine, which has a reseed fallback for exactly
        # this — reseeding here would discard the lineage that live clients are
        # still generating updates against.
        return None, mr.merged, False
    restamp_block_ids(doc, mr.merged)
    return doc.get_update(before), mr.merged, True


__all__ = [
    "SessionGone",
    "initial_sync_message",
    "read_body",
    "rebase_delta",
    "state_vector_client_ids",
    "sync_reply",
    "validate_update",
    "YMessageType",
    "read_message",
]

"""Provenance service. Classifies who produced each wiki commit, records it
through the ``app/db/provenance`` repo, and resolves attribution for reads.

One append-only ledger row per ``(commit_sha, doc_path)`` is written from the
commit gateway (``commit_and_fan_out``) right after a commit lands, so human
saves, agent tool calls, and connector ingestion are all captured in one place.
Commits that bypass the gateway (seeding, ``.gitkeep`` creates, move and trash
commits) have no row, and readers fall back to the git author. This is the
structured record the byline, History, and the Sources tab read from.

For ingestion, ``source_ranges`` maps the spans an ingest commit changed to the
document it came from, anchored like a comment so it survives later edits.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher
from typing import Any

from app.auth.users import UserKind
from app.db import provenance as repo
from app.models.wiki import ActorKind, Attribution, SourceRef, SourceSpan, WriteProvenance
from app.wiki import git as wiki_git, provenance_remap
from app.wiki.constants import INGEST_AUTHOR

log = logging.getLogger(__name__)

# The ledger's source columns, named once by the model that mirrors them.
_SOURCE_FIELDS = tuple(WriteProvenance.model_fields)

# Cap the ingest-row scan behind sources_for_path so a heavily re-ingested page
# cannot load an unbounded result set to dedup down to a handful of documents. A
# source last seen beyond this many recent ingest commits drops off the list.
_SOURCES_SCAN_LIMIT = 500

_VIA_RE = re.compile(r"^(.*?)\s+via\s+(.+)$", re.IGNORECASE)
_INGEST_NAME = INGEST_AUTHOR.split(" <", 1)[0]


def _actor_kind(
    user_kind: str | None, agent_name: str | None, source: WriteProvenance | None
) -> ActorKind:
    """Classify a write from the identity facts in scope at commit time.

    A ``WriteProvenance`` marks an ingestion write and wins over everything,
    even when the connector omitted the source document id. Otherwise a write
    with no bound user, or one made by a non-person principal, is ``system``,
    and a bound agent name separates an agent write from a plain human save.
    """
    if source is not None:
        return ActorKind.INGESTION
    if user_kind is None or user_kind == UserKind.SYSTEM.value:
        return ActorKind.SYSTEM
    if agent_name:
        return ActorKind.AGENT
    return ActorKind.HUMAN


def record(
    *,
    commit_sha: str,
    doc_path: str,
    user_id: str | None,
    agent_name: str | None,
    agent_session_id: str | None,
    source: WriteProvenance | None,
) -> int | None:
    """Record one provenance row for a landed commit and return its ledger id.

    Idempotent on ``(commit_sha, doc_path)`` via the repo: a re-delivered task
    reuses the existing row's id rather than erroring on the write path.
    """
    kind = repo.user_kind(user_id) if user_id else None
    return repo.insert_ledger(
        commit_sha=commit_sha,
        doc_path=doc_path,
        actor_kind=_actor_kind(kind, agent_name, source).value,
        user_id=user_id,
        agent_name=agent_name,
        agent_session_id=agent_session_id,
        source_values=source.model_dump() if source else {},
    )


# --------------------------------------------------------------------------- #
# Source ranges: map an ingest commit's changed spans to its source document    #
# --------------------------------------------------------------------------- #


def changed_spans(old_body: str, new_body: str) -> list[tuple[int, int]]:
    """Character spans in ``new_body`` that differ from ``old_body`` (the
    replace and insert hunks), for attributing an ingest commit's edits to the
    document it came from."""
    return [
        (j1, j2)
        for tag, _i1, _i2, j1, j2 in SequenceMatcher(
            None, old_body, new_body, autojunk=False
        ).get_opcodes()
        if tag in ("replace", "insert")
    ]


def capture_source_ranges(
    *, provenance_id: int, doc_path: str, anchor_sha: str, old_body: str, new_body: str
) -> None:
    """Record which spans of ``new_body`` an ingest commit changed, linked to its
    ledger row. Idempotent per ledger row: the repo replaces any existing ranges,
    so re-processing a commit does not duplicate them."""
    rows = [
        {
            "doc_path": doc_path,
            "anchor_sha": anchor_sha,
            "start_offset": start,
            "end_offset": end,
            "quoted_text": new_body[start:end],
        }
        for start, end in changed_spans(old_body, new_body)
    ]
    repo.replace_source_ranges(provenance_id, rows)


# --------------------------------------------------------------------------- #
# Read side: resolve a commit's provenance for the byline, History, Sources    #
# --------------------------------------------------------------------------- #


def _agent_label(raw: str) -> str:
    """Display label for an agent, from either a stored slug or a git author
    tail. Unrecognized agents keep the name they came with."""
    key = raw.strip().lower().removeprefix("launcher-")
    if "claude" in key:
        return "Claude Code"
    if "codex" in key or "openai" in key:
        return "Codex"
    if "onyx" in key or "craft" in key:
        return "Onyx Craft"
    return raw.strip() or "agent"


def _from_author(author: str) -> Attribution:
    """Parse a git author name into an Attribution. The fallback for any commit
    with no ledger row."""
    name = author.strip()
    if name == _INGEST_NAME:
        return Attribution(actor_kind=ActorKind.INGESTION)
    m = _VIA_RE.match(name)
    if m:
        person = m.group(1).strip() or "Unknown"
        return Attribution(
            actor_kind=ActorKind.AGENT, person=person, agent=_agent_label(m.group(2))
        )
    return Attribution(actor_kind=ActorKind.HUMAN, person=name or "Unknown")


def _source_facts(row: dict[str, Any]) -> dict[str, Any]:
    return {f: row[f] for f in _SOURCE_FIELDS}


def _to_attribution(row: dict[str, Any]) -> Attribution:
    return Attribution(
        actor_kind=ActorKind(row["actor_kind"]),
        person=row["owner_display"],
        agent=_agent_label(row["agent_name"]) if row["agent_name"] else None,
        **_source_facts(row),
    )


def for_commits(commit_shas: Iterable[str], doc_path: str) -> dict[str, Attribution]:
    """Ledger attribution for the given commits on a path, keyed by sha. One
    batched query behind a history render."""
    rows = repo.ledger_rows_for_commits(list(commit_shas), doc_path)
    return {row["commit_sha"]: _to_attribution(row) for row in rows}


def for_history(rows: Sequence[wiki_git.CommitInfo], doc_path: str) -> dict[str, Attribution]:
    """Attribution for every commit in a history render, keyed by sha. The
    ledger row where there is one, a parse of the git author name where there
    is not."""
    hits = for_commits([r.sha for r in rows], doc_path)
    return {r.sha: hits.get(r.sha) or _from_author(r.author) for r in rows}


def head_attribution(doc_path: str, head_sha: str | None) -> Attribution | None:
    """Attribution for a page's current HEAD commit, for the byline."""
    if head_sha is None:
        return None
    hit = for_commits([head_sha], doc_path).get(head_sha)
    if hit is not None:
        return hit
    meta = wiki_git.last_commit_meta_for_path(doc_path)
    return _from_author(meta[1]) if meta else None


def sources_for_path(doc_path: str) -> list[SourceRef]:
    """Distinct ingested documents still credited to a page, newest first,
    deduped on source document id (falling back to url or title). Rows carrying
    none of the three cannot be told apart, so each is kept.

    A source stays listed until there is positive evidence its content is gone:
    the repo drops a row only once every span its ingest captured has been
    retired. A row whose ingest captured no spans (a no-content ingest, or one
    made before span capture existed) carries no such evidence, so it stays.
    """
    seen: set[str] = set()
    out: list[SourceRef] = []
    for row in repo.ingestion_source_rows(doc_path, _SOURCES_SCAN_LIMIT):
        key = row["source_document_id"] or row["source_url"] or row["source_title"]
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(SourceRef(last_updated=row["created_at"], **_source_facts(row)))
    return out


def content_spans_for_path(doc_path: str) -> list[SourceSpan]:
    """Live spans of a page mapped to the documents they were ingested from, for
    in-document highlighting. Stale spans are remapped to HEAD first, and only
    spans anchored at HEAD are returned, so every offset lines up with the
    page's current body. A remap hiccup degrades to fewer highlights, never a
    failed read or a misplaced one."""
    head_sha = wiki_git.head_sha_for_path(doc_path)
    if head_sha is None:
        return []
    try:
        provenance_remap.remap_source_ranges(doc_path)
    except Exception:
        log.exception("source range remap failed for %s", doc_path)
    return [SourceSpan(**row) for row in repo.live_spans_for_doc(doc_path, head_sha)]

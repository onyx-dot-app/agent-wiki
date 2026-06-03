"""Build the context payload we hand to the NL evaluator and renderer.

Layout (a single string):

    === WIKI (latest version) ===
    --- <path-1>
    <body-1>
    --- <path-2>
    <body-2>
    ...

    === CHANGE ===
    Path: <doc_path>
    Kind: <create|edit|...>

    <unified diff or "(new file)" + body>

The wiki snapshot gives the evaluator and renderer cross-doc context (so a
trigger can reason about how the changed doc relates to its siblings).
The CHANGE block is what the trigger should usually evaluate against —
the prompt instructs the model to focus on the diff unless the trigger
description is explicitly about overall state.

Token budget is bounded by per-doc and total caps so a runaway wiki
can't blow the model's context window.
"""
from __future__ import annotations

import difflib
import logging

from app.wiki import git as wiki_git
from app.models.wiki import ChangeKind

log = logging.getLogger(__name__)

# Per-document body cap inside the wiki snapshot. Keeps any single doc from
# dominating the context even if everything else stays small.
_PER_DOC_BUDGET = 16_000
# Soft cap on the total wiki snapshot. Once we cross it we stop emitting
# more docs and append a "[truncated …]" tail.
_WIKI_TOTAL_BUDGET = 200_000
# Per-side budget for the CHANGE block bodies (used for high-density rewrites
# where the unified diff isn't a useful summary).
_CHANGE_BODY_BUDGET = 8_000
# A diff covering more than this fraction of the file isn't a useful summary;
# fall back to passing the bodies straight through.
_DIFF_DENSITY_FALLBACK = 0.5
# Soft cap on the whole CHANGES-SINCE block. Keeps a busy window from
# crowding the snapshot out of the model's context.
_CHANGES_TOTAL_BUDGET = 60_000


def build_wiki_snapshot() -> str:
    """Concat every tracked `.md` doc's current body. Bounded by budgets above.

    Walks ``git ls-files`` so the snapshot reflects committed state, not the
    working tree (matters during a fan-out: BEFORE/AFTER read at SHAs while
    the snapshot reads HEAD-equivalent paths).
    """
    chunks: list[str] = ["=== WIKI (latest version) ==="]
    total = len(chunks[0])
    truncated = 0
    for path in wiki_git.list_paths():
        if not path.endswith(".md"):
            continue
        if total >= _WIKI_TOTAL_BUDGET:
            truncated += 1
            continue
        try:
            body = wiki_git.read_file(path)
        except Exception:
            log.warning("snapshot: skip unreadable %s", path, exc_info=True)
            continue
        body = _truncate(body, _PER_DOC_BUDGET)
        block = f"--- {path}\n{body.rstrip()}\n"
        chunks.append(block)
        total += len(block)
    if truncated:
        chunks.append(f"[truncated {truncated} more docs to fit budget]\n")
    return "\n".join(chunks)


def build_change_view(
    *, doc_path: str, change_kind: ChangeKind, before: str, after: str
) -> str:
    """The "what changed" block: a unified diff for edits, full body for creates."""
    header = f"=== CHANGE ===\nPath: {doc_path}\nKind: {change_kind.value}\n"

    if change_kind == ChangeKind.CREATE or not before:
        body = _truncate(after, _CHANGE_BODY_BUDGET)
        return f"{header}\n(new file — full body)\n{body.rstrip()}\n"

    diff = _unified_diff(before, after)
    if diff and _diff_density(diff, after) <= _DIFF_DENSITY_FALLBACK:
        truncated_diff = _truncate(diff, _CHANGE_BODY_BUDGET)
        return f"{header}\n<unified diff>\n{truncated_diff.rstrip()}\n</unified diff>\n"

    # High-density rewrite: diff is noise; show both bodies side by side.
    return (
        f"{header}\n"
        f"<wholesale rewrite — diff omitted>\n\n"
        f"BEFORE:\n{_truncate(before, _CHANGE_BODY_BUDGET).rstrip()}\n\n"
        f"AFTER:\n{_truncate(after, _CHANGE_BODY_BUDGET).rstrip()}\n"
    )


def build_new_file_view(*, doc_path: str, body: str) -> str:
    """The "new file" block: just the path and the new file's body, no diff."""
    truncated = _truncate(body, _CHANGE_BODY_BUDGET)
    return (
        f"=== NEW FILE ===\nPath: {doc_path}\n\n{truncated.rstrip()}\n"
    )


def build_new_file_payload(
    *,
    doc_path: str,
    body: str,
    wiki_snapshot: str | None = None,
) -> str:
    """Payload for directory-scoped triggers when a brand-new file appears.

    No diff section — the diff would just be the body with ``+`` on every
    line, which is noise. Show the new file as itself instead.
    """
    snapshot = wiki_snapshot if wiki_snapshot is not None else build_wiki_snapshot()
    nf = build_new_file_view(doc_path=doc_path, body=body)
    return f"{snapshot}\n\n{nf}"


def build_payload(
    *,
    doc_path: str,
    change_kind: ChangeKind,
    before: str,
    after: str,
    wiki_snapshot: str | None = None,
) -> str:
    """Compose the full evaluator/renderer payload.

    ``wiki_snapshot`` is built once per fan-out (it's the same for every
    trigger on a given commit) and threaded through. If omitted we build
    it inline — convenient for tests / direct invocation.
    """
    snapshot = wiki_snapshot if wiki_snapshot is not None else build_wiki_snapshot()
    change = build_change_view(
        doc_path=doc_path, change_kind=change_kind, before=before, after=after
    )
    return f"{snapshot}\n\n{change}"


def build_schedule_payload(
    *,
    scope_path: str,
    when_iso: str,
    since_iso: str | None = None,
    wiki_snapshot: str | None = None,
) -> str:
    """Payload for a schedule-kind trigger evaluation.

    A schedule tick isn't tied to a single commit, so we give the LLM the
    full wiki snapshot for overall-state conditions. When ``since_iso`` is
    provided (the previous tick / last fire), we also append a CHANGES
    SINCE LAST CHECK block — the diffs committed under ``scope_path`` over
    ``[since_iso, when_iso]`` — so the trigger can reason about *change*
    over the window ("a new doc appeared", "X was updated since yesterday")
    and not just current state. The trailing SCHEDULED CHECK block names
    the scope and tick time.
    """
    snapshot = wiki_snapshot if wiki_snapshot is not None else build_wiki_snapshot()
    scope_label = scope_path or "(whole wiki)"
    parts = [snapshot]
    if since_iso is not None:
        parts.append(build_changes_since(scope_path=scope_path, since_iso=since_iso))
    parts.append(
        f"=== SCHEDULED CHECK ===\n"
        f"Scope: {scope_label}\n"
        f"Time: {when_iso}\n"
    )
    return "\n\n".join(parts)


def _in_scope(path: str, scope_path: str) -> bool:
    """Is ``path`` under the trigger's scope? Empty scope = whole wiki."""
    if not scope_path:
        return True
    if path == scope_path:
        return True
    return path.startswith(scope_path.rstrip("/") + "/")


def _read_or_empty(path: str, ref: str) -> str:
    """Body of ``path`` at ``ref``, or ``""`` if it didn't exist there
    (a brand-new file at the window start, or a path deleted by HEAD)."""
    return wiki_git.read_file_opt(path, ref) or ""


def _change_entry(path: str, before: str, after: str) -> str | None:
    """One ``--- <path> (kind)`` entry for the changes-since block. Returns
    ``None`` when there's no effective change (touched then reverted)."""
    if before == after:
        return None
    if not before:
        body = _truncate(after, _CHANGE_BODY_BUDGET)
        return f"--- {path}  (new file)\n{body.rstrip()}\n"
    if not after:
        return f"--- {path}  (deleted)\n"
    diff = _unified_diff(before, after)
    if diff and _diff_density(diff, after) <= _DIFF_DENSITY_FALLBACK:
        return f"--- {path}  (edited)\n{_truncate(diff, _CHANGE_BODY_BUDGET).rstrip()}\n"
    # High-density rewrite: the diff is noise; show both bodies.
    return (
        f"--- {path}  (rewritten)\n"
        f"BEFORE:\n{_truncate(before, _CHANGE_BODY_BUDGET).rstrip()}\n\n"
        f"AFTER:\n{_truncate(after, _CHANGE_BODY_BUDGET).rstrip()}\n"
    )


def build_changes_since(*, scope_path: str, since_iso: str) -> str:
    """The CHANGES SINCE LAST CHECK block for a schedule fire.

    Diffs every ``.md`` doc under ``scope_path`` committed since
    ``since_iso`` (the previous tick / last fire) against its body at the
    window start, reusing the delta path's unified-diff + density-fallback
    logic. New files show their full body; deletions are noted. The block
    is explicitly "(no changes in this window)" when nothing matched, so
    the model can tell "nothing changed" apart from "I wasn't given a diff".
    """
    header = f"=== CHANGES SINCE LAST CHECK (since {since_iso}) ==="
    empty = f"{header}\n(no changes in this window)\n"
    before_ref = wiki_git.rev_before(since_iso)
    touched = sorted(
        p
        for p in wiki_git.paths_touched_since(since_iso)
        if p.endswith(".md") and _in_scope(p, scope_path)
    )
    if not touched:
        return empty

    chunks = [header]
    total = len(header)
    truncated = 0
    for path in touched:
        if total >= _CHANGES_TOTAL_BUDGET:
            truncated += 1
            continue
        after = _read_or_empty(path, "HEAD")
        before = _read_or_empty(path, before_ref) if before_ref else ""
        entry = _change_entry(path, before, after)
        if entry is None:
            continue
        chunks.append(entry)
        total += len(entry)
    if truncated:
        chunks.append(f"[truncated {truncated} more changed docs to fit budget]\n")
    if len(chunks) == 1:
        return empty
    return "\n".join(chunks)


def _unified_diff(before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            n=2,
        )
    )


def _diff_density(diff: str, after: str) -> float:
    if not after:
        return 1.0
    return len(diff) / max(len(after), 1)


def _truncate(s: str, budget: int) -> str:
    if len(s) <= budget:
        return s
    return s[:budget] + f"\n…[truncated {len(s) - budget} chars]"

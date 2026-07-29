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


def build_scope_block(scope_path: str) -> str:
    """The current bodies of the doc(s) under the trigger's scope — the
    payload's lead block. A ``.md`` scope is that one doc; a folder scope is
    every doc under it; an empty scope is the whole wiki. Bounded by budgets
    above; walks ``git ls-files`` so the block reflects committed state.
    """
    label = scope_path or "(whole wiki)"
    chunks: list[str] = [f"=== SCOPED DOCS (latest version) ===\nScope: {label}\n"]
    total = len(chunks[0])
    truncated = 0
    for path in wiki_git.list_paths():
        if not path.endswith(".md") or not _in_scope(path, scope_path):
            continue
        if total >= _WIKI_TOTAL_BUDGET:
            truncated += 1
            continue
        try:
            body = wiki_git.read_file(path)
        except Exception:
            log.warning("scope block: skip unreadable %s", path, exc_info=True)
            continue
        block = f"--- {path}\n{_truncate(body, _PER_DOC_BUDGET).rstrip()}\n"
        chunks.append(block)
        total += len(block)
    if truncated:
        chunks.append(f"[truncated {truncated} more docs to fit budget]\n")
    if len(chunks) == 1:
        chunks.append("(no docs found under this scope)\n")
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
    scope_path: str,
    scope_block: str | None = None,
) -> str:
    """Payload for directory-scoped triggers when a brand-new file appears.

    No diff section — the diff would just be the body with ``+`` on every
    line, which is noise. Show the new file as itself instead. The payload
    carries only the trigger's scoped docs, never the whole wiki.
    """
    lead = scope_block if scope_block is not None else build_scope_block(scope_path)
    nf = build_new_file_view(doc_path=doc_path, body=body)
    return f"{lead}\n\n{nf}"


def build_payload(
    *,
    doc_path: str,
    change_kind: ChangeKind,
    before: str,
    after: str,
    scope_path: str,
    scope_block: str | None = None,
) -> str:
    """Compose the full evaluator/renderer payload: the trigger's scoped docs
    plus the change view. ``scope_block`` lets a fan-out reuse one block per
    distinct scope instead of rebuilding it for every trigger."""
    lead = scope_block if scope_block is not None else build_scope_block(scope_path)
    change = build_change_view(
        doc_path=doc_path, change_kind=change_kind, before=before, after=after
    )
    return f"{lead}\n\n{change}"


def build_schedule_payload(
    *,
    scope_path: str,
    when_iso: str,
    since_iso: str | None = None,
    scope_block: str | None = None,
) -> str:
    """Payload for a schedule-kind trigger evaluation.

    A schedule tick isn't tied to a single commit, so the payload leads with
    the scoped docs' current bodies for overall-state conditions. When
    ``since_iso`` is provided (the previous tick / last fire), a CHANGES
    SINCE LAST CHECK block follows — the diffs committed under ``scope_path``
    over ``[since_iso, when_iso]`` — so the trigger can reason about *change*
    over the window and not just current state. The trailing SCHEDULED CHECK
    block names the scope and tick time.
    """
    scope_label = scope_path or "(whole wiki)"
    parts = [scope_block if scope_block is not None else build_scope_block(scope_path)]
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


def _change_entry(
    path: str, before: str, after: str, budget: int = _CHANGE_BODY_BUDGET
) -> str | None:
    """One ``--- <path> (kind)`` entry for the changes-since block. Returns
    ``None`` when there's no effective change (touched then reverted).

    ``budget`` is per rendered body/diff; the schedule path passes a dynamic
    value (few docs changed → each gets most of the block budget), because a
    fixed cap head-truncates long pages and silently drops the tail — the
    exact place a long page's newest edits tend to live.

    Rendering prefers the unified diff even when it's dense: hunks carry the
    changed regions wherever they sit in the page, while BEFORE/AFTER bodies
    lose everything past the truncation point. The bodies fallback is kept
    only for the degenerate case where the diff is literally bigger than
    showing both bodies."""
    if before == after:
        return None
    if not before:
        body = _truncate(after, budget)
        return f"--- {path}  (new file)\n{body.rstrip()}\n"
    if not after:
        return f"--- {path}  (deleted)\n"
    diff = _unified_diff(before, after)
    if diff and len(diff) <= len(before) + len(after):
        return f"--- {path}  (edited)\n{_truncate(diff, budget).rstrip()}\n"
    # Degenerate: the diff exceeds both bodies together — show the bodies,
    # sharing the entry budget between them so one rewritten entry can never
    # weigh ~2x its allowance (an oversized payload risks a model-context
    # error, which skips the fire while the window advances).
    half = max(budget // 2, 1)
    return (
        f"--- {path}  (rewritten)\n"
        f"BEFORE:\n{_truncate(before, half).rstrip()}\n\n"
        f"AFTER:\n{_truncate(after, half).rstrip()}\n"
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

    # Gather the bodies first and drop net-zero paths (touched then
    # reverted) BEFORE dividing the budget — they render nothing, and
    # counting them would dilute the genuine docs' share back toward the
    # static cap.
    changes: list[tuple[str, str, str]] = []
    for path in touched:
        after = _read_or_empty(path, "HEAD")
        # The doc may have been renamed within the window; read its *before*
        # body at the name it had at ``before_ref``, or the diff degrades to a
        # spurious "(new file)" and hides the real edit.
        before = ""
        if before_ref:
            before_path = wiki_git.path_at_ref(path, before_ref) or path
            before = _read_or_empty(before_path, before_ref)
        if before != after:
            changes.append((path, before, after))
    if not changes:
        return empty

    chunks = [header]
    total = len(header)
    truncated = 0
    # Dynamic per-entry budget: the block budget shared across the docs that
    # actually changed, floored at the static per-body cap. A quiet window
    # that touched one long page gets to show that page's changes whole
    # instead of head-truncating them (the standup-trigger miss: the page's
    # newest edits sat past the fixed cap and the LLM gate never saw them).
    per_entry = max(_CHANGE_BODY_BUDGET, _CHANGES_TOTAL_BUDGET // len(changes))
    for path, before, after in changes:
        if total >= _CHANGES_TOTAL_BUDGET:
            truncated += 1
            continue
        entry = _change_entry(path, before, after, budget=per_entry)
        if entry is None:
            continue
        chunks.append(entry)
        total += len(entry)
    if truncated:
        chunks.append(f"[truncated {truncated} more changed docs to fit budget]\n")
    if len(chunks) == 1:
        return empty
    return "\n".join(chunks)


def change_touches_lines(
    before: str, after: str, start_line: int, end_line: int
) -> bool:
    """Does the edit touch the 1-based inclusive ``[start_line, end_line]``
    of the *current* body? Insertions and replacements count where they land
    in the new file; a pure deletion counts at the line it collapses onto.
    New files count if the range exists in the body; deletions of the whole
    file always count (the watched lines are gone)."""
    if before == after:
        return False
    if not after:
        return True
    after_lines = after.splitlines()
    if not before:
        return start_line <= len(after_lines)
    matcher = difflib.SequenceMatcher(
        a=before.splitlines(keepends=True), b=after.splitlines(keepends=True)
    )
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # New-file line span of this edit; a deletion (j1 == j2) collapses
        # onto line j1, so treat it as touching that single position.
        lo = j1 + 1
        hi = j2 if j2 > j1 else min(j1 + 1, len(after_lines))
        if lo > hi:
            lo = hi
        if hi >= start_line and lo <= end_line:
            return True
    return False


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

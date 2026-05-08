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
    *, doc_path: str, change_kind: str, before: str, after: str
) -> str:
    """The "what changed" block: a unified diff for edits, full body for creates."""
    header = f"=== CHANGE ===\nPath: {doc_path}\nKind: {change_kind}\n"

    if change_kind == "create" or not before:
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
    change_kind: str,
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

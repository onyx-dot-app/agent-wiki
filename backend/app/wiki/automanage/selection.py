"""Selection — step 3 of the sweep's reconciliation pipeline.

After *detect all* (every detector over the scope) and *dedup* (map each
finding onto its identity), selection decides which findings become the
live slate and which are persisted-but-invalid. Two mechanisms live here:

**Claims.** A proposal's claim is every path it touches — sources, targets,
reserved names — compared case-insensitively (case-insensitive filesystems
are a hazard we detect, never manufacture) and subtree-aware (a folder
claims everything under it, and anything under it claims the folder).
Selected proposals have pairwise-disjoint claims, which yields "at most one
live proposal per page" as the common case and makes the slate
order-independent: any subset can be approved and executed in any order
without one proposal racing another for a path. A claim is exactly the
agentic applier's sandbox (the executor's allowed-set), so the selection
lock and the execution scope-check are one definition read at two times.

**Cooldown.** A rejection quiets its content-free scope — the ``dedup_key``
prefix before the premise — for ``SUBJECT_COOLDOWN_DAYS``: a *new* premise
on freshly rejected pages is detected and persisted but unselectable, so
content churn can't turn into immediate re-asks after a human said no.
(The *same* premise never returns at all — that's dedup's job.)

Deliberately mechanical (set intersections and one timestamp compare) and
**pure** where it counts: `conflicts` and claim construction take values and
return values, so slate composition is unit-testable without a database.
Full doctrine: the "Wiki Auto Management — Dedup" design page.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.wiki import change_proposals

# The post-rejection quiet window on a subject: long enough that periodic
# content syncs don't nag, short enough that a real new situation resurfaces
# within a quarter.
SUBJECT_COOLDOWN_DAYS = 30


def claim_of(paths: list[str]) -> frozenset[str]:
    """The normalized claim for a proposal's paths: case-folded, deduped.
    Subtree semantics live in :func:`conflicts`, not in the set itself."""
    return frozenset(p.casefold() for p in paths)


def conflicts(claim: frozenset[str], blocked: frozenset[str]) -> bool:
    """True when ``claim`` overlaps ``blocked`` — equal paths, or one side
    holding an ancestor folder of the other's path. Both sets are already
    case-folded (see :func:`claim_of`)."""
    if claim & blocked:
        return True
    for c in claim:
        prefix = c + "/"
        for b in blocked:
            if b.startswith(prefix) or c.startswith(b + "/"):
                return True
    return False


def cooldown_until(dedup_key: str, *, days: int | None = None) -> str | None:
    """If the key's content-free scope was rejected within the window, the
    UTC ``YYYY-MM-DD HH:MM:SS`` timestamp when the cooldown ends — else None.

    The scope is everything before the premise (the final ``|``-segment), so
    a rejection of one premise quiets *different* premises on the same
    (detector, op, documents). The exact same premise never reaches here —
    dedup suppresses it outright."""
    if days is None:
        days = SUBJECT_COOLDOWN_DAYS  # read at call time — tunable/patchable
    prefix = dedup_key.rsplit("|", 1)[0] + "|"
    rejected = change_proposals.latest_rejection_with_dedup_prefix(prefix)
    if rejected is None:
        return None
    _rejected_id, rejected_at = rejected
    try:
        ts = datetime.strptime(rejected_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    until = ts + timedelta(days=days)
    if datetime.now(UTC) >= until:
        return None
    return until.strftime("%Y-%m-%d %H:%M:%S")

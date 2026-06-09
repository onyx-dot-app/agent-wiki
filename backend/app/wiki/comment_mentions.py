"""@mention tokens inside a comment body (server-side mirror of the frontend's
``lib/commentMentions.ts``).

Mentions are stored inline in the comment ``body`` as a canonical token:

    @[Display Name](mention:<user_id>)

The frontend (de)tokenizes at the compose/edit boundary; the backend stores the
body verbatim. For search indexing we need the body in human-readable form (so
``@[Bo Yang](mention:cmt_x)`` is searched as "Bo Yang") plus the set of
mentioned user ids (for a future "mentions of me" filter).
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"@\[([^\]]+)\]\(mention:([^)]+)\)")


def detokenize(body: str) -> str:
    """Replace each mention token with its display form ``@Name`` so the text
    indexes/searches naturally."""
    return _TOKEN_RE.sub(lambda m: f"@{m.group(1)}", body)


def mentioned_ids(body: str) -> list[str]:
    """The distinct user ids mentioned in ``body`` (order-preserving)."""
    seen: dict[str, None] = {}
    for m in _TOKEN_RE.finditer(body):
        seen.setdefault(m.group(2), None)
    return list(seen)

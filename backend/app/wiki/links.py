"""Markdown link checking for the wiki.

Lightweight stand-in for an LSP — after a write, we scan the new body for
``[text](relative.md)`` links, resolve them against the wiki working tree,
and surface broken targets back to the model.

Only relative `.md` links are checked. Absolute URLs (``http(s)://``,
``mailto:``, anchors-only) and inline images are ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.wiki import filesystem


# Match ``[text](target)`` not preceded by a ``!`` (image syntax).
# Allows balanced parens inside text/target only at one nesting level —
# good enough for typical wiki prose.
_LINK_RE = re.compile(r"(?<!\!)\[([^\]]+)\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class BrokenLink:
    text: str       # link text inside the brackets
    target: str     # raw href as it appeared in the markdown
    resolved: str   # wiki-relative path we tried to resolve to


def find_broken_links(body: str, doc_path: str) -> list[BrokenLink]:
    """Return links whose targets don't exist in the wiki working tree.

    ``doc_path`` is the wiki-relative path of the document being checked
    (e.g. ``"auth/passwords.md"``); relative link targets are resolved
    against its directory.
    """
    base_dir = Path(doc_path).parent  # may be Path('.')

    broken: list[BrokenLink] = []
    seen: set[str] = set()

    for match in _LINK_RE.finditer(body):
        text, raw_target = match.group(1), match.group(2)
        if not _is_relative_md_link(raw_target):
            continue

        target_path, _anchor = _split_anchor(raw_target)
        if not target_path:
            # ``#anchor``-only links — same-doc, can't validate without an
            # anchor index. Skip.
            continue

        # Strip query strings if any.
        target_path = target_path.split("?", 1)[0]

        try:
            resolved = filesystem.safe_rel_path(str((base_dir / target_path)))
        except ValueError:
            # Escaping the wiki root counts as broken.
            key = (text, raw_target, target_path)
            if key in seen:
                continue
            seen.add(key)
            broken.append(BrokenLink(text=text, target=raw_target, resolved=target_path))
            continue

        abs_path = filesystem.absolute(resolved)
        if abs_path.is_file():
            continue

        key = (text, raw_target, resolved)
        if key in seen:
            continue
        seen.add(key)
        broken.append(BrokenLink(text=text, target=raw_target, resolved=resolved))

    return broken


def _is_relative_md_link(target: str) -> bool:
    if target.startswith(("http://", "https://", "mailto:", "ftp://", "//")):
        return False
    if target.startswith("#"):
        return False
    if target.startswith("/"):
        return False
    base = target.split("#", 1)[0].split("?", 1)[0]
    return base.endswith(".md")


def _split_anchor(target: str) -> tuple[str, str]:
    if "#" in target:
        path, _, anchor = target.partition("#")
        return path, anchor
    return target, ""

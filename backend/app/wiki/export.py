"""Markdown export — single pages and folder subtrees as downloadable files.

Bodies are exported verbatim except for internal links: absolute app links
(``/app/wiki/<path>``) are rewritten to paths relative to the containing
document, so cross-links resolve inside the extracted tree instead of
pointing back at the app. Zip entries keep their full wiki-relative paths
for the same reason.

Only ``.md`` pages are exported. Postgres-only state (ACLs, comments,
provenance, update policies) is not part of an export — exported content
carries no permissions.
"""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from urllib.parse import quote, unquote

from app.auth import User
from app.wiki import acl, filesystem
from app.wiki import git as wiki_git

_APP_ROUTE_PREFIX = "/app/wiki/"

# Match ``[text](target)`` not preceded by ``!`` (image syntax). Unlike the
# broken-link checker's pattern, the target may contain spaces — internal
# links in the corpus are written unencoded.
_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")


def _is_dot_metadata(path: str) -> bool:
    """Any dot-prefixed component marks app metadata, never a wiki page —
    even with a ``.md`` suffix (e.g. ``.metadata.md``, ``.meta/state.md``)."""
    return any(part.startswith(".") for part in path.split("/"))


def visible_md_paths(user: User, prefix: str = "") -> list[str]:
    """Tracked ``.md`` pages under ``prefix`` that ``user`` can read."""
    md = [
        p
        for p in wiki_git.list_paths(prefix)
        if p.endswith(".md") and not _is_dot_metadata(p)
    ]
    return acl.filter_paths_in_python(user.id, user.is_admin, md)


def rewrite_links(body: str, doc_path: str) -> str:
    """Rewrite absolute app links to paths relative to ``doc_path``.

    ``[B](/app/wiki/docs/b.md)`` inside ``docs/a.md`` becomes ``[B](b.md)``.
    Percent-encoded targets are decoded to the corpus's unencoded style;
    anchors are preserved. Relative and external links pass through as-is.
    """
    doc_dir = posixpath.dirname(doc_path)

    def _sub(match: re.Match[str]) -> str:
        text, target = match.group(1), match.group(2)
        relativized = _relativize(target, doc_dir)
        if relativized is None:
            return match.group(0)
        return f"[{text}]({relativized})"

    return _LINK_RE.sub(_sub, body)


def _relativize(target: str, doc_dir: str) -> str | None:
    """Relative form of an ``/app/wiki/`` target, or None to leave it alone."""
    path_part, sep, anchor = target.strip().partition("#")
    decoded = unquote(path_part)
    if not decoded.startswith(_APP_ROUTE_PREFIX):
        return None
    rel = decoded[len(_APP_ROUTE_PREFIX) :]
    if not rel:
        return None
    return posixpath.relpath(rel, start=doc_dir or ".") + (sep + anchor)


def export_page(rel_path: str) -> str | None:
    """Body of one page with links rewritten; None if it isn't on disk."""
    abs_path = filesystem.absolute(rel_path)
    if not abs_path.is_file():
        return None
    return rewrite_links(abs_path.read_text(), rel_path)


def build_zip(rel_paths: list[str]) -> bytes:
    """Zip of the given pages, entries keyed by wiki-relative path.

    Built in memory: exports are read-only, occasional, and bounded by the
    visible page set, so a buffer beats plumbing a streaming response
    through the router. Revisit if wikis outgrow this.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in rel_paths:
            body = export_page(rel)
            if body is not None:
                zf.writestr(rel, body)
    return buf.getvalue()


def content_disposition(filename: str) -> str:
    """``Content-Disposition`` attachment header value for ``filename``.

    Wiki paths are routinely non-ASCII, so emit both the plain ``filename``
    fallback (ASCII-squashed) and the RFC 5987 ``filename*`` form.
    """
    ascii_name = (
        filename.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "")
        or "export"
    )
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )

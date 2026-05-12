"""Drafting state — tracks "user is still drafting initial version from
a template" per wiki page.

A row exists from the moment a page is created via a template until the
body diverges from the template snapshot (i.e. the user saved real
edits). The chat widget uses this to show a drafting banner and to
override its system prompt with the template's, if any.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db.models import DocumentDraft, DocumentTemplate
from app.db.session import session

log = logging.getLogger(__name__)


def _to_dict(d: DocumentDraft, t: DocumentTemplate | None) -> dict[str, Any]:
    return {
        "path": d.path,
        "template_id": d.template_id,
        "template_name": t.name if t else None,
        "system_prompt": t.system_prompt if t else None,
        "template_body_snapshot": d.template_body_snapshot,
        "created_by_user_id": d.created_by_user_id,
        "created_at": d.created_at,
    }


def get(path: str) -> dict[str, Any] | None:
    with session() as s:
        row = s.get(DocumentDraft, path)
        if row is None:
            return None
        tmpl = s.get(DocumentTemplate, row.template_id)
        return _to_dict(row, tmpl)


def create(
    *,
    path: str,
    template_id: str,
    template_body_snapshot: str,
    created_by_user_id: str | None,
) -> None:
    """Insert a new draft row; replaces any existing row for ``path``."""
    with session() as s:
        existing = s.get(DocumentDraft, path)
        if existing is not None:
            existing.template_id = template_id
            existing.template_body_snapshot = template_body_snapshot
            existing.created_by_user_id = created_by_user_id
        else:
            s.add(
                DocumentDraft(
                    path=path,
                    template_id=template_id,
                    template_body_snapshot=template_body_snapshot,
                    created_by_user_id=created_by_user_id,
                )
            )
    log.info("draft created path=%s template_id=%s", path, template_id)


def delete(path: str) -> bool:
    with session() as s:
        row = s.get(DocumentDraft, path)
        if row is None:
            return False
        s.delete(row)
        return True


def clear_if_diverged(path: str, current_body: str) -> bool:
    """Clear the draft row if ``current_body`` differs from the snapshot.

    Returns True when the row was cleared, False if no row existed or
    the body still matches.
    """
    with session() as s:
        row = s.get(DocumentDraft, path)
        if row is None:
            return False
        if row.template_body_snapshot == current_body:
            return False
        s.delete(row)
    log.info("draft cleared path=%s (body diverged)", path)
    return True


def list_all() -> list[dict[str, Any]]:
    """For tests/debugging."""
    with session() as s:
        rows = s.scalars(select(DocumentDraft)).all()
        out: list[dict[str, Any]] = []
        for r in rows:
            t = s.get(DocumentTemplate, r.template_id)
            out.append(_to_dict(r, t))
        return out

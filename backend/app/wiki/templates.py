"""Document templates repo — admin-managed seed content for new wiki pages.

Each row defines a named markdown template that a user picks when
creating a fresh ``.md`` document. The template's body becomes the
initial commit; ``system_prompt`` (when set) overrides the chat
agent's default prompt while the user is still drafting (see
``app.wiki.drafts``).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import DocumentTemplate
from app.db.session import session

log = logging.getLogger(__name__)


class TemplateNameTaken(Exception):
    """Raised when a template name conflicts with an existing row."""


def _to_dict(t: DocumentTemplate) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "body": t.body,
        "description": t.description,
        "system_prompt": t.system_prompt,
        "created_by_user_id": t.created_by_user_id,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def list_all() -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(
            select(DocumentTemplate).order_by(DocumentTemplate.name.asc())
        ).all()
        return [_to_dict(r) for r in rows]


def get(template_id: str) -> dict[str, Any] | None:
    with session() as s:
        row = s.get(DocumentTemplate, template_id)
        return _to_dict(row) if row else None


def create(
    *,
    name: str,
    body: str,
    description: str | None,
    system_prompt: str | None,
    created_by_user_id: str | None,
) -> dict[str, Any]:
    template_id = str(uuid.uuid4())
    with session() as s:
        s.add(
            DocumentTemplate(
                id=template_id,
                name=name,
                body=body,
                description=description,
                system_prompt=system_prompt,
                created_by_user_id=created_by_user_id,
            )
        )
        try:
            s.flush()
        except IntegrityError as exc:
            raise TemplateNameTaken(name) from exc
    log.info("template created id=%s name=%s", template_id, name)
    row = get(template_id)
    assert row is not None
    return row


def update(
    template_id: str,
    *,
    name: str,
    body: str,
    description: str | None,
    system_prompt: str | None,
) -> dict[str, Any] | None:
    with session() as s:
        t = s.get(DocumentTemplate, template_id)
        if t is None:
            return None
        t.name = name
        t.body = body
        t.description = description
        t.system_prompt = system_prompt
        t.updated_at = _now_text(s)
        try:
            s.flush()
        except IntegrityError as exc:
            raise TemplateNameTaken(name) from exc
        return _to_dict(t)


def delete(template_id: str) -> bool:
    with session() as s:
        t = s.get(DocumentTemplate, template_id)
        if t is None:
            return False
        s.delete(t)
        return True


def count() -> int:
    with session() as s:
        return s.scalar(select(func.count()).select_from(DocumentTemplate)) or 0


def _now_text(s: Any) -> str:
    """ISO timestamp matching the column's server_default."""
    return s.scalar(
        select(func.to_char(func.timezone("UTC", func.now()), "YYYY-MM-DD HH24:MI:SS"))
    )

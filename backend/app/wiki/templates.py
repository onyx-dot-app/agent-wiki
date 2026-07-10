"""Document templates repo — admin-managed seed content for new wiki pages.

Each row defines a named markdown template that a user picks when
creating a fresh ``.md`` document. The template's body becomes the
initial commit; ``system_prompt`` (when set) overrides the chat
agent's default prompt while the user is still drafting (see
``app.wiki.drafts``).

Fresh-install seeding: ``seed_starter_templates_if_empty`` reads the
bundled ``backend/starter_templates/*.md`` files on first boot and
inserts a row per template. Each file uses a tiny YAML frontmatter
(``name``, ``description``, optional ``system_prompt``) followed by
the markdown body. The seed is one-shot — it only runs when the
``document_templates`` table is empty, so a user who deletes a
starter template will not see it re-appear after a reboot. The CLI
in ``app/scripts/seed_starter_templates.py`` shares the loader to
layer the bundled set into an already-populated install.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import DocumentTemplate
from app.db.session import session
from app.wiki import update_policy

log = logging.getLogger(__name__)


# /app/starter_templates in the image; backend/starter_templates in dev.
# Mirrors how ``app.wiki.seed.SEED_SOURCE_DIR`` is resolved.
STARTER_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[2] / "starter_templates"
)

# The empty-start template a new page defaults to when no template is picked.
# It's the create-time default, so it can't be deleted or renamed (which would
# break the by-name lookup) — admins may still edit its body/policy.
_BLANK_TEMPLATE_NAME = "Blank"


class _UnsetType:
    """Sentinel: a field omitted from ``update`` keeps its stored value."""


_UNSET = _UnsetType()


class TemplateNameTaken(Exception):
    """Raised when a template name conflicts with an existing row."""


class ProtectedTemplateError(Exception):
    """Raised when deleting or renaming a template the app depends on."""


def _to_dict(t: DocumentTemplate) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "body": t.body,
        "description": t.description,
        "system_prompt": t.system_prompt,
        "ingestion_auto_update_disabled": t.ingestion_auto_update_disabled,
        "ai_management_allowed": t.ai_management_allowed,
        "update_instruction": t.update_instruction,
        "sort_order": t.sort_order,
        "created_by_user_id": t.created_by_user_id,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def list_all() -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(
            select(DocumentTemplate).order_by(
                DocumentTemplate.sort_order.asc(),
                DocumentTemplate.name.asc(),
            )
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
    ingestion_auto_update_disabled: bool | None = None,
    ai_management_allowed: bool | None = None,
    update_instruction: str | None = None,
    created_by_user_id: str | None,
) -> dict[str, Any]:
    template_id = str(uuid.uuid4())
    with session() as s:
        # New rows land at the end of the picker — one past the current
        # max so admins decide where they live by reordering afterwards.
        current_max = s.scalar(
            select(func.coalesce(func.max(DocumentTemplate.sort_order), -1))
        )
        next_order = (current_max if current_max is not None else -1) + 1
        s.add(
            DocumentTemplate(
                id=template_id,
                name=name,
                body=body,
                description=description,
                system_prompt=system_prompt,
                ingestion_auto_update_disabled=ingestion_auto_update_disabled,
                ai_management_allowed=ai_management_allowed,
                update_instruction=update_instruction,
                sort_order=next_order,
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
    ingestion_auto_update_disabled: bool | None | _UnsetType = _UNSET,
    ai_management_allowed: bool | None | _UnsetType = _UNSET,
    update_instruction: str | None | _UnsetType = _UNSET,
) -> dict[str, Any] | None:
    with session() as s:
        t = s.get(DocumentTemplate, template_id)
        if t is None:
            return None
        if t.name == _BLANK_TEMPLATE_NAME and name != _BLANK_TEMPLATE_NAME:
            raise ProtectedTemplateError(
                f"the {_BLANK_TEMPLATE_NAME!r} template can't be renamed — it's "
                "the default new pages start from"
            )
        t.name = name
        t.body = body
        t.description = description
        t.system_prompt = system_prompt
        # Omitted (``_UNSET``) policy fields keep their stored value, so a
        # client that doesn't send them can't silently clear a template's policy.
        if not isinstance(ingestion_auto_update_disabled, _UnsetType):
            t.ingestion_auto_update_disabled = ingestion_auto_update_disabled
        if not isinstance(ai_management_allowed, _UnsetType):
            t.ai_management_allowed = ai_management_allowed
        if not isinstance(update_instruction, _UnsetType):
            t.update_instruction = update_instruction
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
        if t.name == _BLANK_TEMPLATE_NAME:
            raise ProtectedTemplateError(
                f"the {_BLANK_TEMPLATE_NAME!r} template can't be deleted — it's "
                "the default new pages start from"
            )
        s.delete(t)
        return True


def blank_template_id() -> str | None:
    """Id of the bundled empty-start ``Blank`` template, or None if absent.

    The default a new page starts from when the caller didn't pick a template,
    so every page begins from a template (Blank carries auto-update off)."""
    with session() as s:
        return s.scalar(
            select(DocumentTemplate.id).where(
                DocumentTemplate.name == _BLANK_TEMPLATE_NAME
            )
        )


def apply_policy_to_page(
    path: str, template_id: str, actor_user_id: str | None
) -> bool:
    """Seed a page's update policy from a template (auto-update default +
    update instruction). Returns False if the template doesn't exist.

    Only the fields the template actually sets are written; the rest stay
    inherited. Shared by the new-doc create path (``api/wiki.py``) and the
    agent ``write_doc`` tool."""
    tmpl = get(template_id)
    if tmpl is None:
        return False
    patch: dict[str, Any] = {}
    if tmpl.get("ingestion_auto_update_disabled") is not None:
        patch["ingestion_auto_update_disabled"] = tmpl["ingestion_auto_update_disabled"]
    if tmpl.get("update_instruction"):
        patch["update_instruction"] = tmpl["update_instruction"]
    if tmpl.get("ai_management_allowed") is not None:
        patch["ai_management_allowed"] = tmpl["ai_management_allowed"]
    if patch:
        update_policy.set_policy(path, actor_user_id=actor_user_id, **patch)
    return True


class ReorderMismatch(Exception):
    """Raised when ``reorder`` is called with a set of ids that does not
    exactly match the current ``document_templates`` rows."""


def reorder(template_ids: list[str]) -> None:
    """Set ``sort_order`` to the index of each id in ``template_ids``.

    Requires the caller to pass *every* current template id exactly
    once — partial reorders would silently leave rows behind at stale
    positions. The admin UI builds this list from its already-loaded
    view, so the contract is easy to satisfy.
    """
    if len(set(template_ids)) != len(template_ids):
        raise ReorderMismatch("duplicate template ids in reorder request")
    with session() as s:
        existing: set[str] = set(s.scalars(select(DocumentTemplate.id)).all())
        if set(template_ids) != existing:
            missing = existing - set(template_ids)
            extra = set(template_ids) - existing
            raise ReorderMismatch(
                f"reorder must list every current template exactly once "
                f"(missing={sorted(missing)} extra={sorted(extra)})"
            )
        for index, tid in enumerate(template_ids):
            row = s.get(DocumentTemplate, tid)
            assert row is not None  # guarded by the set-equality check above
            row.sort_order = index
    log.info("reordered %d templates", len(template_ids))


def count() -> int:
    with session() as s:
        return s.scalar(select(func.count()).select_from(DocumentTemplate)) or 0


def _now_text(s: Any) -> str:
    """ISO timestamp matching the column's server_default."""
    return s.scalar(
        select(func.to_char(func.timezone("UTC", func.now()), "YYYY-MM-DD HH24:MI:SS"))
    )


# --------------------------------------------------------------------------- #
# Starter-template seeding                                                    #
# --------------------------------------------------------------------------- #


# Default picker order applied on the fresh-install seed. Names listed
# here go first in this exact order; bundled templates not in the list
# fall in afterwards in alphabetical order. The order is admin-editable
# from the templates admin page after seeding — this only sets the
# initial position on a brand-new database.
_STARTER_TEMPLATE_DEFAULT_ORDER: tuple[str, ...] = (
    "Blank",
    "Weekly notes",
    "Project tracker",
    "Product Requirements Doc",
    "Sales opportunity",
    "Meeting notes",
    "Architecture Decision Record",
    "Incident report",
    "Runbook",
)


class StarterTemplateParseError(Exception):
    """Raised when a bundled starter-template file is malformed."""


def _parse_frontmatter(text: str, source: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-delimited YAML frontmatter from the markdown body.

    Returns ``(metadata, body)``. The metadata block is required to
    declare ``name`` (the unique template name) and may declare
    ``description`` and ``system_prompt``. ``source`` is used in error
    messages.
    """
    if not text.startswith("---"):
        raise StarterTemplateParseError(
            f"{source}: missing YAML frontmatter (file must start with '---')"
        )
    # Skip the opening ``---`` line, then split on the next one.
    after_open = text.split("\n", 1)[1] if "\n" in text else ""
    parts = after_open.split("\n---", 1)
    if len(parts) != 2:
        raise StarterTemplateParseError(
            f"{source}: frontmatter is not closed with a '---' line"
        )
    raw_meta, rest = parts
    try:
        raw: object = yaml.safe_load(raw_meta) or {}
    except yaml.YAMLError as exc:
        raise StarterTemplateParseError(f"{source}: invalid YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise StarterTemplateParseError(
            f"{source}: frontmatter must be a YAML mapping, got {type(raw).__name__}"
        )
    meta = cast(dict[str, Any], raw)
    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        raise StarterTemplateParseError(f"{source}: frontmatter is missing 'name'")
    # Strip the leading newline after the closing ``---`` so the body
    # starts cleanly at the first content line.
    body = rest.lstrip("\n")
    return meta, body


def iter_starter_templates() -> list[dict[str, Any]]:
    """Parse every bundled starter-template file into a dict.

    Returns rows shaped like ``{name, description, system_prompt, body}``.
    The iteration order is the seed-time picker order: names in
    ``_STARTER_TEMPLATE_DEFAULT_ORDER`` come first, then any other
    bundled templates alphabetically. Admins can re-order from the UI
    afterwards — this only matters on the fresh-install seed.
    """
    if not STARTER_TEMPLATES_DIR.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for src in sorted(STARTER_TEMPLATES_DIR.glob("*.md")):
        meta, body = _parse_frontmatter(src.read_text(), source=src.name)
        rows.append(
            {
                "name": meta["name"].strip(),
                "description": meta.get("description"),
                "system_prompt": meta.get("system_prompt"),
                "ingestion_auto_update_disabled": meta.get(
                    "ingestion_auto_update_disabled"
                ),
                "update_instruction": meta.get("update_instruction"),
                "body": body,
            }
        )

    def _order_key(row: dict[str, Any]) -> tuple[int, str]:
        try:
            return (_STARTER_TEMPLATE_DEFAULT_ORDER.index(row["name"]), "")
        except ValueError:
            # Not in the preferred list — sort after, alphabetically.
            return (len(_STARTER_TEMPLATE_DEFAULT_ORDER), row["name"])

    rows.sort(key=_order_key)
    return rows


def _existing_names() -> set[str]:
    with session() as s:
        rows: list[str] = list(s.scalars(select(DocumentTemplate.name)).all())
        return set(rows)


def write_starter_templates(*, skip_existing: bool) -> int:
    """Insert each bundled starter template via the normal ``create`` path.

    When ``skip_existing`` is True, templates whose ``name`` already
    exists in the table are skipped (useful for the CLI that layers
    the bundled set onto an existing install). Returns the number of
    rows actually inserted.
    """
    inserted = 0
    existing: set[str] = _existing_names() if skip_existing else set()
    for row in iter_starter_templates():
        if skip_existing and row["name"] in existing:
            log.info("starter template skip %s (already exists)", row["name"])
            continue
        try:
            create(
                name=row["name"],
                body=row["body"],
                description=row["description"],
                system_prompt=row["system_prompt"],
                ingestion_auto_update_disabled=row["ingestion_auto_update_disabled"],
                update_instruction=row["update_instruction"],
                created_by_user_id=None,
            )
        except TemplateNameTaken:
            log.info("starter template skip %s (name taken)", row["name"])
            continue
        inserted += 1
    return inserted


def seed_starter_templates_if_empty() -> bool:
    """Seed the bundled starter templates if no templates exist yet.

    One-shot: only runs when ``document_templates`` is empty, so a
    user who deletes a starter template will not see it re-appear on
    the next reboot. Returns True if any rows were inserted.
    """
    if not STARTER_TEMPLATES_DIR.is_dir():
        log.debug("no bundled starter templates at %s, skipping", STARTER_TEMPLATES_DIR)
        return False
    if count() > 0:
        log.debug("document_templates already populated, skipping starter seed")
        return False
    inserted = write_starter_templates(skip_existing=False)
    if inserted:
        log.info("seeded %d starter document templates", inserted)
    return inserted > 0

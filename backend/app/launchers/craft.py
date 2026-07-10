"""Shared Craft session launch workflow: gate on the admin Onyx URL,
require the caller's connection, ACL-check the source page, dedupe
in-flight launches, cap provisioning, seed the first turn, and enqueue
the sandbox launch."""
from __future__ import annotations

import logging

from app.db import agent_sessions as sessions_repo
from app.ingest import settings as ingest_settings
from app.launchers import prompt_builder
from app.launchers.registry import get_registry
from app.onyx import connections
from app.tasks.craft import attachment_filename, craft_launch
from app.wiki import acl as wiki_acl
from app.wiki import filesystem as wiki_fs

log = logging.getLogger(__name__)

TOOL_ID = "onyx-craft"

# Modest per-user backstop on concurrent sandbox provisioning. The
# idempotency probe already dedups repeat launches for the same (user, page).
MAX_PROVISIONING_PER_USER = 3


class CraftLaunchError(Exception):
    """A launch precondition failed. Subclasses name the reason."""


class CraftUnavailable(CraftLaunchError):
    """The feature is dark: no admin-configured Onyx base URL."""


class CraftMisconfigured(CraftLaunchError):
    """The onyx-craft manifest is missing or not in_app."""


class CraftNotConnected(CraftLaunchError):
    """The user has no usable Onyx connection for the current origin."""


class CraftInvalidPath(CraftLaunchError):
    """The wiki_path failed path validation."""


class CraftForbidden(CraftLaunchError):
    """The user cannot read the source page."""


class CraftRateLimited(CraftLaunchError):
    """The user is at the provisioning cap."""


def require_available() -> str:
    """The admin-configured Onyx origin. Raises CraftUnavailable when dark."""
    base = ingest_settings.get_onyx_base_url()
    if not base:
        raise CraftUnavailable()
    return base


def start_session(
    *,
    user_id: str,
    is_admin: bool,
    wiki_path: str | None,
    message: str,
    reuse_ready: bool = True,
) -> tuple[str, str]:
    """Launch a Craft session for ``user_id`` seeded with ``message``, or
    return the in-flight one for the same page. ``wiki_path`` attaches the
    page to the sandbox. Returns ``(session_id, status)``.

    ``reuse_ready=False`` narrows the idempotency probe to sessions still
    provisioning: a finished (ready) session then never blocks a new launch.
    Repeated trigger fires need that, while the launch button reuses the
    live session.

    Raises a CraftLaunchError subclass on a failed precondition and lets a
    failed enqueue propagate after marking the session failed.
    """
    base = require_available()
    manifest = get_registry().get(TOOL_ID)
    if manifest is None or manifest.kind != "in_app":
        raise CraftMisconfigured()
    if connections.get_with_pat(user_id, onyx_base_url=base) is None:
        raise CraftNotConnected()

    path: str | None = None
    if wiki_path is not None:
        try:
            path = wiki_fs.safe_rel_path(wiki_path)
        except ValueError as exc:
            raise CraftInvalidPath() from exc
        if not wiki_acl.can(user_id, is_admin, "read", path):
            raise CraftForbidden()

    # Idempotency: an in-flight launch for the same (user, page) is returned
    # as-is, never a second sandbox.
    statuses = ("provisioning", "ready") if reuse_ready else ("provisioning",)
    existing = sessions_repo.find_in_flight(
        user_id, tool_id=TOOL_ID, wiki_path=path, statuses=statuses
    )
    if existing is not None:
        return str(existing["id"]), str(existing["status"])

    if sessions_repo.count_provisioning(user_id, tool_id=TOOL_ID) >= MAX_PROVISIONING_PER_USER:
        raise CraftRateLimited()

    seed = prompt_builder.build_craft_seed_prompt(
        attachment_filename=attachment_filename(path) if path else None,
        user_message=message,
    )
    sid = sessions_repo.create(
        user_id=user_id,
        tool_id=TOOL_ID,
        first_turn_prompt=seed,
        wiki_path=path,
        working_dir=None,
        status="provisioning",
    )
    try:
        craft_launch(sid)
    except Exception:
        # The 'provisioning' row is already committed and a failed enqueue
        # means the worker never runs, so mark it failed rather than stranding
        # find_in_flight on a session that can never progress.
        log.exception("craft launch enqueue failed session=%s", sid)
        sessions_repo.mark_craft_failed(sid, reason="provisioning_failed")
        raise
    log.info("craft session %s enqueued user=%s page=%s", sid, user_id, path)
    return sid, "provisioning"

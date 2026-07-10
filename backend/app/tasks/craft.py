"""Background launch of an Onyx Craft build session.

One task: ``craft_launch(agent_session_id)``. ``start_session``
(app/launchers/craft.py) creates the ``AgentSession`` row
(status='provisioning') and enqueues this. The worker then, as the
launching user (their stored PAT):

1. creates the Onyx build session (BLOCKS on sandbox provisioning, ~10-60s),
2. uploads the wiki page body as ``attachments/<page>.md``,
3. fires the seed prompt via ``send-message`` — Onyx detaches the turn into
   its background runner, so nothing here holds a stream open,
4. flips the row to ``ready`` + writes the ``craft_ready`` notification.

Every failure lands as status='failed' + a structured ``failure_reason``
from the taxonomy (auth_expired / org_at_capacity / onyx_unreachable /
provisioning_failed) + a ``craft_failed`` notification. The task never
raises — a queue-level retry after we've marked the row failed would only
fight the user's own Retry button.

Step (1) is guarded for re-delivery: the Onyx session id is persisted the
moment it exists, and the seed send is skipped when the session already
has messages — exactly one sandbox + one seed turn per launch.
"""

from __future__ import annotations

import logging
import re

from app.db import agent_sessions as sessions_repo
from app.db import notifications as notifications_repo
from app.ingest import settings as ingest_settings
from app.onyx import connections
from app.onyx.client import (
    OnyxAuthError,
    OnyxCapacityError,
    OnyxClient,
    OnyxError,
    OnyxUnreachableError,
)
from app.tasks.queues import triggers_queue
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)

NOTIF_CRAFT_READY = "craft_ready"
NOTIF_CRAFT_FAILED = "craft_failed"

_MAX_ATTACHMENT_NAME = 80


def attachment_filename(wiki_path: str) -> str:
    """Sandbox-safe filename for the uploaded page body."""
    base = wiki_path.rsplit("/", 1)[-1]
    if base.endswith(".md"):
        base = base[: -len(".md")]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "page"
    return safe[:_MAX_ATTACHMENT_NAME] + ".md"


def _page_title(wiki_path: str | None) -> str | None:
    if not wiki_path:
        return None
    base = wiki_path.rsplit("/", 1)[-1]
    return base[: -len(".md")] if base.endswith(".md") else base


def _fail(sid: str, *, user_id: str, wiki_path: str | None, reason: str) -> None:
    sessions_repo.mark_craft_failed(sid, reason=reason)
    title = _page_title(wiki_path)
    notifications_repo.create(
        user_id=user_id,
        notif_type=NOTIF_CRAFT_FAILED,
        title=f'Craft launch failed — "{title}"' if title else "Craft launch failed",
        description=None,
        data={"agent_session_id": sid, "failure_reason": reason},
    )


# Long-running (~10-60s on Onyx sandbox provisioning).
@triggers_queue.task()
def craft_launch(agent_session_id: str) -> None:
    row = sessions_repo.get(agent_session_id)
    if row is None:
        log.warning("craft_launch: session %s missing", agent_session_id)
        return
    if row["status"] != "provisioning":
        # Re-delivery after we already finished (ready/failed) — nothing to do.
        return

    sid: str = row["id"]
    user_id: str = row["user_id"]
    wiki_path: str | None = row["wiki_path"]

    base = ingest_settings.get_onyx_base_url()
    if not base:
        _fail(sid, user_id=user_id, wiki_path=wiki_path, reason="provisioning_failed")
        return

    conn = connections.get_with_pat(user_id, onyx_base_url=base)
    if conn is None:
        _fail(sid, user_id=user_id, wiki_path=wiki_path, reason="auth_expired")
        return

    client = OnyxClient(base, conn["onyx_pat"])
    try:
        external_id: str | None = row["external_session_id"]
        if not external_id:
            external_id = client.create_build_session()
            sessions_repo.set_external_session(sid, external_id)
            # Name it after the wiki page so Onyx doesn't list it as "Session <id>".
            # Best-effort: a rename failure must never orphan a provisioned sandbox.
            title = _page_title(wiki_path)
            if title:
                try:
                    client.set_session_name(external_id, name=title)
                except OnyxError:
                    log.warning("craft_launch: set_session_name failed session=%s", sid)

        if wiki_path is not None:
            try:
                body = wiki_git.read_file(wiki_path)
            except Exception:
                # Page deleted/moved between launch and worker — proceed
                # without the attachment rather than failing the launch.
                log.warning("craft_launch: page %s unreadable; launching without it", wiki_path)
                body = None
            if body is not None:
                client.upload_attachment(
                    external_id,
                    filename=attachment_filename(wiki_path),
                    content=body.encode("utf-8"),
                )

        if client.session_message_count(external_id) == 0:
            client.send_seed_message(external_id, content=row["first_turn_prompt"])

        external_url = f"{base}/craft/v1?sessionId={external_id}"
        sessions_repo.mark_craft_ready(sid, external_url=external_url)
        title = _page_title(wiki_path)
        notifications_repo.create(
            user_id=user_id,
            notif_type=NOTIF_CRAFT_READY,
            title=f'Craft is ready — "{title}"' if title else "Craft is ready",
            description="Open Craft to watch the run.",
            data={"agent_session_id": sid, "link": external_url},
        )
        log.info("craft_launch ready session=%s onyx_session=%s", sid, external_id)
    except OnyxAuthError:
        log.warning("craft_launch auth failure session=%s; dropping connection", sid)
        connections.remove(user_id)
        _fail(sid, user_id=user_id, wiki_path=wiki_path, reason="auth_expired")
    except OnyxCapacityError:
        _fail(sid, user_id=user_id, wiki_path=wiki_path, reason="org_at_capacity")
    except OnyxUnreachableError:
        _fail(sid, user_id=user_id, wiki_path=wiki_path, reason="onyx_unreachable")
    except OnyxError:
        log.exception("craft_launch onyx error session=%s", sid)
        _fail(sid, user_id=user_id, wiki_path=wiki_path, reason="provisioning_failed")
    except Exception:
        log.exception("craft_launch unexpected error session=%s", sid)
        _fail(sid, user_id=user_id, wiki_path=wiki_path, reason="provisioning_failed")

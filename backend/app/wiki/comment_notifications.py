"""Fans a new comment or reply out to the people it concerns — the page
owner and any @mentioned users who can read the page, never the author —
as queued notification emails. Recipients who haven't opted in are dropped
by the send task."""
from __future__ import annotations

import logging
from typing import Any

from app.tasks.notify_emails import send_notification_email
from app.wiki import acl
from app.wiki import comment_mentions
from app.wiki.links import doc_url

log = logging.getLogger(__name__)


def queue_for_comment(row: dict[str, Any], *, author_id: str) -> None:
    doc_path = str(row.get("doc_path") or "")
    body = str(row.get("body") or "")
    owner = acl.get_owner(doc_path)
    # A mention alone doesn't grant access: mail only mentioned users who can
    # already read the page, so comment content never leaks past the ACL.
    recipients = {
        uid
        for uid in comment_mentions.mentioned_ids(body)
        if uid == owner or acl.can(uid, False, "read", doc_path)
    }
    if owner:
        recipients.add(owner)
    recipients.discard(author_id)
    if not recipients:
        return
    subject = f"Agent Wiki: new comment on {doc_path}"
    text = (
        f"{comment_mentions.detokenize(body)}\n\n"
        f"— comment on {doc_path}\n{doc_url(doc_path)}"
    )
    for user_id in sorted(recipients):
        send_notification_email(
            user_id=user_id, kind="comment", subject=subject, text=text
        )
